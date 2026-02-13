import redis
import aiohttp
import logging
import asyncio
import datetime
import os
import json
import operator
from typing import TypedDict, Annotated, List, Union
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# LiveKit & AI Plugins
from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    AgentServer,
    JobContext,
    WorkerOptions,
    cli,
    ConversationItemAddedEvent,
    function_tool,
    JobExecutorType
)
from livekit.plugins import silero, deepgram, openai, cartesia
from livekit.protocol.sip import TransferSIPParticipantRequest

# LangGraph
from langgraph.graph import StateGraph, END

load_dotenv()

# --- Configurations ---
logger = logging.getLogger("livekit.agents")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://admin:secretpassword@localhost:27017/?authSource=admin")
# mongo_client = AsyncIOMotorClient(MONGO_URL)
# db = mongo_client.asterisk 
# transcript_collection = db.conversation_history 
r = redis.Redis(host='localhost', port=6379, decode_responses=True)




server = AgentServer(job_executor_type=JobExecutorType.THREAD)
# --- LangGraph: Memory & State ---
class KavyaState(TypedDict):
    messages: Annotated[List[dict], operator.add]
    step_index: int
    vici_id: str
    candidate_name: str
    transfer_failed: bool

async def recruitment_node(state: KavyaState):
    idx = state.get("step_index", 1)
    prefix = ""
    if state.get("transfer_failed"):
        prefix = "I'm sorry I couldn't connect you to a specialist. Let's continue. "
    
    step_text = r.hget(f"step:{idx}", "text") or "Thank you for speaking with us today."
    final_text = step_text.replace("{{consumer_name}}", state.get("candidate_name", "Candidate"))
    
    return {
        "messages": [{"role": "assistant", "content": f"{prefix}{final_text}"}],
        "transfer_failed": False
    }

workflow = StateGraph(KavyaState)
workflow.add_node("recruiter", recruitment_node)
workflow.set_entry_point("recruiter")
graph_app = workflow.compile()

# --- Utility Functions ---
async def start_recording(room_name, vici_id, collection):
    lkapi = api.LiveKitAPI(
        os.getenv('LIVEKIT_URL', "").replace('ws', 'http'), 
        os.getenv('LIVEKIT_API_KEY'), 
        os.getenv('LIVEKIT_API_SECRET'))
    try:
        file_out = api.EncodedFileOutput(file_type=api.EncodedFileType.MP3, filepath=f"/out/{vici_id}.mp3")
        request = api.RoomCompositeEgressRequest(room_name=room_name, audio_only=True, file_outputs=[file_out])
        response = await lkapi.egress.start_room_composite_egress(request)
        await collection.update_one({"call_id": vici_id}, {"$set": {"egress_id": getattr(response, 'egress_id', 'unknown')}}, upsert=True)
    except Exception as e: logger.error(f"Egress Error: {e}")
    finally: await lkapi.aclose()

async def save_message_to_call(collection, data):
    # 1. Define the base update fields
    update_query = {
        "$push": {
            "messages": {
                "role": data["role"], 
                "text": data["text"], 
                "timestamp": datetime.datetime.utcnow()
            }
        },
        "$set": {
            "name": data.get("name", "Candidate"),
            "phone_no": data.get("phone_no", "Unknown"),
            "room": data.get("room"),
            "status": "active"
        },
        "$setOnInsert": {
            "created_at": datetime.datetime.utcnow()
            # Removed step_index from here to avoid conflict
        }
    }

    # 2. Only increment when the assistant speaks
    if data["role"] == "assistant":
        update_query["$inc"] = {"step_index": 1}
    else:
        # If it's a user message and the document is brand new, 
        # ensure step_index exists without conflicting with $inc
        # We use $setOnInsert here ONLY for user messages
        update_query["$setOnInsert"]["step_index"] = 1

    await collection.update_one(
        {"call_id": data["vici_id"]},
        update_query,
        upsert=True
    )

async def cleanup_call(vici_id, collection, room_name, lk_api):
    try:
        # 1. Update DB Status immediately (Prevents it being "Active")
        await collection.update_one(
            {"call_id": vici_id}, 
            {"$set": {
                
                "ended_at": datetime.datetime.utcnow(),
                "status": "yet_to_evaluate",  # Evaluator waits for this
                "ready_for_eval": True
            }}
        )

        # 2. External API cleanup (Redis clear-data)
        async with aiohttp.ClientSession() as sess:
            await sess.delete(f"http://192.168.1.61:9001/clear-data/{vici_id}", timeout=3)

        # 3. DELETE THE ROOM (Your specific requirement)
        try:
            logger.info(f"Deleting room: {room_name}")
            await lk_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception as e:
            # Usually happens if the participant hangup already triggered room closure
            logger.debug(f"Room deletion handled by LiveKit: {e}")

        # 4. Wait for Audio Egress to finalize (Crucial for the Evaluator)
        # 5. SIGNAL EVALUATOR (The Evaluator script should look for this flag)
        await asyncio.sleep(5)

        logger.info(f"✅ Cleanup and Room Deletion complete for {vici_id}")

    except Exception as e:
        logger.error(f"Cleanup Error: {e}")

# --- Entrypoint ---
# A decorator is a function that takes another function as input and returns a new function.
@server.rtc_session()
async def entrypoint(ctx: JobContext):
    await ctx.connect()

    mongo_client = AsyncIOMotorClient(MONGO_URL)
    db = mongo_client.asterisk 
    transcript_collection = db.conversation_history 

    lk_api = api.LiveKitAPI(os.getenv('LIVEKIT_URL', "").replace('ws', 'http'), os.getenv('LIVEKIT_API_KEY'), os.getenv('LIVEKIT_API_SECRET'))

    res = await lk_api.room.list_rooms(api.ListRoomsRequest())
    room_count = len(res.rooms)
    print(f"Total Active Rooms: {room_count}")

    me = (room_count%2)
    if me == 0:
        voice_id = "87286a8d-7ea7-4235-a41a-dd9fa6630feb"
        recruiter_role="Rohit"
    else:        
        voice_id = "faf0731e-dfb9-4cfc-8119-259a79b27e12"
        recruiter_role="Kavya"

    participant = await ctx.wait_for_participant()
    vici_unique_id = None
    for _ in range(10): 
        vici_unique_id = participant.attributes.get("vici_id")
        if vici_unique_id: break
        await asyncio.sleep(0.5)

    candidate_name, phone_no = "Candidate","Unknown"
    if vici_unique_id:
        asyncio.create_task(start_recording(ctx.room.name, vici_unique_id,transcript_collection))
        try:
            async with aiohttp.ClientSession() as session_http:
                async with session_http.get(f"http://192.168.1.61:9001/get-data/{vici_unique_id}", timeout=2) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidate_name, phone_no = data.get('field_1', "Candidate"), data.get('field_3', "Unknown")
                        candidate_phone = data.get('field_3', "Unknown")
                        logger.info(f'Candidate Name is:{candidate_name}')
                        logger.info(f"New call connected: Room={ctx.room.name}")
        except Exception: pass
    
    # 1. FETCH PREVIOUS STATE
    # After you get candidate_name and phone_no from your API:
    time_threshold = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    previous_call = await transcript_collection.find_one(
        {"phone_no": phone_no, "status": "completed","ended_at": {"$gte": time_threshold}},
        sort=[("ended_at", -1)] # Get the most recent one
    )

    cleanup_task_started = False

    async def trigger_final_cleanup():
        nonlocal cleanup_task_started
        if cleanup_task_started:
            return
        cleanup_task_started = True
        await cleanup_call(vici_unique_id, transcript_collection, ctx.room.name, lk_api)

    logger.info(f"this is pre{previous_call}")
    if previous_call:
        logger.info(f"🔄 Reconnecting with {candidate_name}. Resuming state...")
        # Restore the conversation context and step
        initial_messages = previous_call.get("messages", [])
        # We start from the next step after where they left off
        initial_step = previous_call.get("step_index", 1) 
        is_reconnection = True

    else:
        initial_messages = []
        initial_step = 1
        is_reconnection = False

       

    state = {
        "messages": initial_messages, 
        "step_index": initial_step, 
        "vici_id": vici_unique_id or "Unknown", 
        "candidate_name": candidate_name, 
        "transfer_failed": False
    }


    @function_tool
    async def transfer_to_agent():
        """ Call this ONLY if the user explicitly asks to speak to a real person, 
        a human, a supervisor, or a specialist. """
        try:
            vici_ext=''
            async with aiohttp.ClientSession() as session_http:
                async with session_http.post(f"http://192.168.1.61:9001/liveagents/", timeout=3) as resp:
                    data = await resp.json()
                    vici_ext = data.get('user')
            logger.info(f"Transfering to the Human agent {vici_ext}")
            if not vici_ext or vici_ext == 'No agents available':
                state["transfer_failed"] = True
                return "All agents busy. Continue interview."

            await session.generate_reply(instructions="Say: 'Connecting you now.'")
            await asyncio.sleep(2)

            transfer_request = TransferSIPParticipantRequest(
                participant_identity=participant.identity,
                room_name=ctx.room.name,
                transfer_to=f"sip:{vici_ext}@192.168.1.63",
                play_dialtone=True
            )
           
            await lk_api.sip.transfer_sip_participant(transfer_request)
            asyncio.create_task(ctx.room.disconnect())
            
        except Exception:
            state["transfer_failed"] = True
            return "Error. Continue interview."

    @function_tool
    async def end_call():
        try: await lk_api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        except: await ctx.room.disconnect()
        return "Call ended."

    all_steps = "".join([f"Step {i}: {r.hget(f'step:{i}', 'text')}\n" for i in range(1, 15) if r.hget(f"step:{i}", "text")])

    # --- Generate Remaining Steps for Reconnection ---
    remaining_steps_list = []
    for i in range(initial_step, 15):
        step_text = r.hget(f"step:{i}", "text")
        if step_text:
            remaining_steps_list.append(f"Step {i}: {step_text}")
    remaining_script = "\n".join(remaining_steps_list)

    # --- Define System Instruction (Run this ONCE) ---
    if is_reconnection:
        system_instruction = (
            f"You are {recruiter_role} from Greet Technologies. This is a RECONNECTION. "
            f"The candidate {candidate_name} is already at Step {initial_step}. "
            "DO NOT start from the beginning. DO NOT introduce yourself. "
            "Tools: transfer_to_agent, end_call. "
            f"Resume from this script:\n{remaining_script}"
        )
    else:
        system_instruction=(f" You are {recruiter_role} from Greet Technologies. You are interviewing {candidate_name}Be concise. If the user asks a personal question, answer it quickly then continue the script Tools: transfer_to_agent (for human requests), end_call (to hang up) If the user is finished Flow: \n" "CRITICAL: When you call a tool (like transfer_to_agent), always report the result or the message returned by the tool back to the user immediately.\n"
        + all_steps )



    
    agent = Agent(instructions=system_instruction, 
    tools=[transfer_to_agent, end_call])


    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"),
        tts=cartesia.TTS(model="sonic-english", voice=voice_id),
        
    )

    # FIX: Use synchronous wrappers for .on() events
    @session.on("user_speech_finished")
    def on_user_speech(event):
        async def process_speech():
            state["messages"].append({"role": "user", "content": event.text})
            
            # 1. Run the graph to get the CURRENT step text
            result = await graph_app.ainvoke(state)
            script_text = result["messages"][-1]["content"]
            
            # 2. ONLY increment AFTER the user has theoretically 
            # finished the current step's interaction.
            state["step_index"] += 1 
            
            await session.generate_reply(instructions=f"Continue the interview with this step: {script_text}")
        
        asyncio.create_task(process_speech())

    # Proper shutdown handling
    async def _on_shutdown():
        if vici_unique_id and not cleanup_task_started:
            logger.info("Shutdown triggered - forcing final cleanup")
            await trigger_final_cleanup()
        await lk_api.aclose()

    ctx.add_shutdown_callback(_on_shutdown)
    # Listeners for logging
    @session.on("conversation_item_added")
    def on_item_added(event: ConversationItemAddedEvent):
        if event.item.text_content:
        # Pass the whole state or just the index
            log = {
                "vici_id": state["vici_id"], 
                "phone_no": phone_no, # Use the variable from outer scope
                "name": state["candidate_name"], 
                "role": event.item.role, 
                "text": event.item.text_content, 
                "room": ctx.room.name
            }
            asyncio.create_task(save_message_to_call(transcript_collection, log))

    @ctx.room.on("participant_disconnected")
    def on_disconnect(p):
        if vici_unique_id:
            # asyncio.shield protects the task from being cancelled 
            # when the room context closes.
            asyncio.ensure_future(asyncio.shield(trigger_final_cleanup()))
        else:
            logger.warning(f"Abrupt disconnect: No vici_id found for room {ctx.room.name}. Manual cleanup may be required.")
        

    # --- START THE SESSION FIRST ---
    await session.start(agent=agent, room=ctx.room)
    
    # --- NOW GREET THE CANDIDATE ---
    if is_reconnection:
        # Ask the LLM to generate a "Welcome back" response instead of the standard greeting
        await session.generate_reply(
            instructions=f"Welcome {candidate_name} back, apologize for the technical glitch, and resume Step {initial_step}."
        )
    else:
        init_res = await graph_app.ainvoke(state)
        await session.generate_reply(instructions=f"Greet the candidate and say: {init_res['messages'][-1]['content']}")

# --- ADD THIS LOAD BALANCER AT THE VERY BOTTOM ---
def compute_load(server: AgentServer) -> float:
    return min(len(server.active_jobs) / 20, 1.0) # Allows 20 concurrent calls

server.load_fnc = compute_load
server.load_threshold = 1.0

if __name__ == "__main__":
    cli.run_app(server)