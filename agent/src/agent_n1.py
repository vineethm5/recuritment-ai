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
    function_tool
)
from livekit.plugins import silero, deepgram, openai, cartesia
from livekit.protocol.sip import TransferSIPParticipantRequest

# LangGraph
from langgraph.graph import StateGraph, END

load_dotenv()

# --- Configurations ---
logger = logging.getLogger("livekit.agents")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://admin:secretpassword@localhost:27017/?authSource=admin")
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client.asterisk 
transcript_collection = db.conversation_history 
r = redis.Redis(host='localhost', port=6379, decode_responses=True)

server = AgentServer()
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
async def start_recording(room_name, vici_id):
    lkapi = api.LiveKitAPI(
        os.getenv('LIVEKIT_URL', "").replace('ws', 'http'), 
        os.getenv('LIVEKIT_API_KEY'), 
        os.getenv('LIVEKIT_API_SECRET'))
    try:
        file_out = api.EncodedFileOutput(file_type=api.EncodedFileType.MP3, filepath=f"/out/{vici_id}.mp3")
        request = api.RoomCompositeEgressRequest(room_name=room_name, audio_only=True, file_outputs=[file_out])
        response = await lkapi.egress.start_room_composite_egress(request)
        await transcript_collection.update_one({"call_id": vici_id}, {"$set": {"egress_id": getattr(response, 'egress_id', 'unknown')}}, upsert=True)
    except Exception as e: logger.error(f"Egress Error: {e}")
    finally: await lkapi.aclose()

async def save_message_to_call(data):
    await transcript_collection.update_one(
        {"call_id": data["vici_id"]},
        {"$push": {"messages": {"role": data["role"], "text": data["text"], "timestamp": datetime.datetime.utcnow()}},
         "$setOnInsert": {"created_at": datetime.datetime.utcnow(), "name": data["name"], "phone_no": data["phone_no"], "room": data["room"], "status": "active"}},
        upsert=True
    )

async def cleanup_call(vici_id):
    try:
        async with aiohttp.ClientSession() as sess:
            await sess.delete(f"http://192.168.1.61:9001/clear-data/{vici_id}")
        await transcript_collection.update_one({"call_id": vici_id}, {"$set": {"status": "completed", "ended_at": datetime.datetime.utcnow()}})
    except Exception as e: logger.error(f"Cleanup Error: {e}")

# --- Entrypoint ---
# A decorator is a function that takes another function as input and returns a new function.
@server.rtc_session()
async def entrypoint(ctx: JobContext):
    await ctx.connect()
    lk_api = api.LiveKitAPI(os.getenv('LIVEKIT_URL', "").replace('ws', 'http'), os.getenv('LIVEKIT_API_KEY'), os.getenv('LIVEKIT_API_SECRET'))

    participant = await ctx.wait_for_participant()
    vici_unique_id = None
    for _ in range(10): 
        vici_unique_id = participant.attributes.get("vici_id")
        if vici_unique_id: break
        await asyncio.sleep(0.5)

    candidate_name, phone_no = "Candidate","Unknown"
    if vici_unique_id:
        asyncio.create_task(start_recording(ctx.room.name, vici_unique_id))
        try:
            async with aiohttp.ClientSession() as session_http:
                async with session_http.get(f"http://192.168.1.61:9001/get-data/{vici_unique_id}", timeout=2) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidate_name, phone_no = data.get('field_1', "Candidate"), data.get('field_3', "Unknown")
                        logger.info(f'Candidate Name is:{candidate_name}')
        except Exception: pass

    state = {"messages": [], "step_index": 1, "vici_id": vici_unique_id or "Unknown", "candidate_name": candidate_name, "transfer_failed": False}

    @function_tool
    async def transfer_to_agent():
        """ Call this ONLY if the user explicitly asks to speak to a real person. """
        try:
            # 1. Get the available agent extension from your API
            vici_ext = ''
            async with aiohttp.ClientSession() as session_http:
                async with session_http.post("http://192.168.1.61:9001/liveagents/", timeout=3) as resp:
                    data = await resp.json()
                    vici_ext = data.get('user')

            if not vici_ext or vici_ext == 'No agents available':
                logger.info("Transfer failed: No agents available.")
                return "Currently, all our specialists are busy. Let's continue our conversation."

            logger.info(f"Transferring to Human agent: {vici_ext}")

            # 2. Inform the user
            await session.generate_reply(instructions="Say: 'One moment, I am connecting you to a specialist now.'")
            await asyncio.sleep(2)

            # 3. Initialize LiveKit API inside the function
            # Ensure your ENV variables for LIVEKIT_API_KEY and SECRET are set
            lk_api_client = api.LiveKitAPI() 

            TRUNK_ID = "ST_5oPz3JBMGjbM" 
            vici_did = "123456789" # This should be the DID routed to your In-Group

            try:
                # Create the SIP Participant (this dials Vicidial and bridges the audio)
                await lk_api_client.sip.create_sip_participant(
                    api.CreateSIPParticipantRequest(
                        sip_trunk_id=TRUNK_ID,
                        sip_call_to=vici_did,
                        room_name=ctx.room.name, # Correctly referencing current room
                        participant_identity=f"transfer_to_{vici_ext}",
                        headers={"X-VC-Payload": vici_ext} # Passing agent ID to Vicidial
                    )
                )
                logger.info("SIP Outbound Dial sent to Vicidial.")
                
                # 4. Wait a moment for the bridge to establish, then disconnect the AI
                await asyncio.sleep(3)
                await ctx.room.disconnect()
                
            except Exception as e:
                logger.error(f"SIP Dial Error: {e}")
                return "I'm having trouble connecting to the line. Let's continue here."
            finally:
                await lk_api_client.aclose()

        except Exception as e:
            logger.error(f"General Transfer Error: {e}")
            return "Connection error. Let's continue our interview."

    @function_tool
    async def end_call():
        try: await lk_api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        except: await ctx.room.disconnect()
        return "Call ended."

    all_steps = "".join([f"Step {i}: {r.hget(f'step:{i}', 'text')}\n" for i in range(1, 15) if r.hget(f"step:{i}", "text")])

    system_instruction=(f" You are Kavya from Greet Technologies. You are interviewing {candidate_name}Be concise. If the user asks a personal question, answer it quickly then continue the script Tools: transfer_to_agent (for human requests), end_call (to hang up) If the user is finished Flow: \n" "CRITICAL: When you call a tool (like transfer_to_agent), always report the result or the message returned by the tool back to the user immediately.\n"
    + all_steps )

    agent = Agent(instructions=system_instruction, 
    tools=[transfer_to_agent, end_call])


    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"),
        tts=cartesia.TTS(model="sonic-english", voice="95d51f79-c397-46f9-b49a-23763d3eaa2d")
    )

    # FIX: Use synchronous wrappers for .on() events
    @session.on("user_speech_finished")
    def on_user_speech(event):
        async def process_speech():
            # Ainvoke LangGraph in parallel with any other prep logic
            state["messages"].append({"role": "user", "content": event.text})
            
            # Start the graph process
            graph_task = asyncio.create_task(graph_app.ainvoke(state))
            
            # While the graph is thinking, you could even send a "Thinking" filler if steps are slow
            result = await graph_task
            state["step_index"] += 1
            script_text = result["messages"][-1]["content"]
            
            # Use a shorter instruction format for the turn
            await session.generate_reply(instructions=f"Continue the interview with this step: {script_text}")
        
        asyncio.create_task(process_speech())

    # ... [rest of your listeners] ...

    await session.start(agent=agent, room=ctx.room)
    
    # Initial Greeting
    init_res = await graph_app.ainvoke(state)
    await session.generate_reply(instructions=f"Greet the candidate and say: {init_res['messages'][-1]['content']}")

    @session.on("conversation_item_added")
    def on_item_added(event: ConversationItemAddedEvent):
        if event.item.text_content:
            log = {"vici_id": state["vici_id"], "phone_no": phone_no, "name": state["candidate_name"], 
                   "role": event.item.role, "text": event.item.text_content, "room": ctx.room.name}
            asyncio.create_task(save_message_to_call(log))

    @ctx.room.on("participant_disconnected")
    def on_disconnect(p):
        if vici_unique_id:
            asyncio.create_task(cleanup_call(vici_unique_id))

    await session.start(agent=agent, room=ctx.room)
    
    # Start Step 1
    init_res = await graph_app.ainvoke(state)
    await session.generate_reply(instructions=f"Say: {init_res['messages'][-1]['content']}")

if __name__ == "__main__":
    cli.run_app(server)