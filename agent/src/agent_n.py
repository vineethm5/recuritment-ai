import redis
import aiohttp
import logging
import asyncio
import datetime
import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import base64
from livekit.protocol.sip import TransferSIPParticipantRequest

from livekit import api
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    cli,
    ConversationItemAddedEvent,
    function_tool # Added this
)
from livekit.plugins import silero, deepgram, openai, cartesia
from openai import OpenAI

load_dotenv()

# --- Configurations ---
MONGO_URL = os.getenv("MONGO_URL", "mongodb://admin:secretpassword@localhost:27017/?authSource=admin")
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client.asterisk 
transcript_collection = db.conversation_history 

r = redis.Redis(host='localhost', port=6379, decode_responses=True)
server = AgentServer()
logger = logging.getLogger("livekit.agents")

# --- Helper Functions ---

async def start_recording(room_name, vici_id):
    """Triggers Egress and updates MongoDB with metadata"""
    url = os.getenv('LIVEKIT_URL', "").replace('ws', 'http')
    
    lkapi = api.LiveKitAPI(
        url,
        os.getenv('LIVEKIT_API_KEY'),
        os.getenv('LIVEKIT_API_SECRET')
    )

    filename = f"{vici_id}.mp3"
    
    try:
        file_out = api.EncodedFileOutput(
            file_type=api.EncodedFileType.MP3,
            filepath=f"/out/{filename}"
        )

        request = api.RoomCompositeEgressRequest(
            room_name=room_name,
            audio_only=True,
            file_outputs=[file_out]
        )

        response = await lkapi.egress.start_room_composite_egress(request)
        egress_id = getattr(response, 'egress_id', 'unknown_id')

        # Update Mongo with Recording Info
        await transcript_collection.update_one(
            {"call_id": vici_id},
            {
                "$set": {
                    "egress_id": egress_id,
                    "recording_file": filename,
                    "updated_at": datetime.datetime.utcnow()
                }
            },
            upsert=True
        )
        logger.info(f"🔴 Egress started: {egress_id} for {vici_id}")
        return egress_id
    except Exception as e:
        logger.error(f"❌ Egress Error for {vici_id}: {e}")
    finally:
        await lkapi.aclose()

async def save_message_to_call(data):
    """Saves individual chat turns to MongoDB"""
    await transcript_collection.update_one(
        {"call_id": data["vici_id"]},
        {
            "$push": {
                "messages": {
                    "role": data["role"],
                    "text": data["text"],
                    "timestamp": datetime.datetime.utcnow()
                }
            },
            "$setOnInsert": {
                "created_at": datetime.datetime.utcnow(),
                "name": data["name"],
                "phone_no": data["phone_no"],
                "room": data["room"],
                "status": "active",
            }
        },
        upsert=True
    )



async def cleanup_call(vici_id):
    """Finalizes database and triggers post-call processing"""
    try:
        # 1. External API Cleanup
        async with aiohttp.ClientSession() as session:
            await session.delete(f"http://192.168.1.61:9001/clear-data/{vici_id}")
        
        # 2. Update status to completed
        await transcript_collection.update_one(
            {"call_id": vici_id},
            {"$set": {"status": "completed", "ended_at": datetime.datetime.utcnow()}}
        )

        logger.info(f"🏁 Finished all post-call tasks for {vici_id}")
    except Exception as e:
        logger.error(f"❌ Cleanup Error: {e}")

@server.rtc_session()
async def entrypoint(ctx: JobContext):
    await ctx.connect()
    
    # Initialize API Client for SIP and Room Management
    lk_api = api.LiveKitAPI(
        os.getenv('LIVEKIT_URL', "").replace('ws', 'http'),
        os.getenv('LIVEKIT_API_KEY'),
        os.getenv('LIVEKIT_API_SECRET')
    )

    participant = await ctx.wait_for_participant()
    vici_unique_id = None
    for _ in range(10): 
        vici_unique_id = participant.attributes.get("vici_id")
        if vici_unique_id: break
        await asyncio.sleep(0.5)

    # --- Integrated Tools ---

    @function_tool
    async def transfer_to_agent():
        """
        Transfers the caller to a live human representative in Vicidial. 
        """
        logger.info(f"Initiating SIP REFER transfer for {participant.identity} to Vicidial")
        
        try:
            # According to the docs, 'tel:' or 'sip:' prefixes are required
            # Since you are dialing a local extension on your Vicidial IP:
            transfer_target = "sip:1234@192.168.1.63" 

            transfer_request = TransferSIPParticipantRequest(
                participant_identity=participant.identity,
                room_name=ctx.room.name,
                transfer_to=transfer_target,
                play_dialtone=False
            )

            # Use the exact method name from the official docs
            await lk_api.sip.transfer_sip_participant(transfer_request)
            
            logger.info(f"SIP REFER sent successfully for {vici_unique_id}")
            
            # The docs mention the caller leaves the room automatically, 
            # but we'll shut down the agent session locally to be safe.
            asyncio.create_task(ctx.room.disconnect())
            
            return "I am connecting you to a representative now. Please stay on the line."

        except Exception as e:
            logger.error(f"Transfer error using TransferSIPParticipantRequest: {e}")
            return "I'm sorry, I'm having trouble transferring your call. Please wait a moment."

    @function_tool
    async def end_call():
        try:
            # 1. Hard Hangup: Delete the room to force SIP BYE
            await lk_api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
            logger.info("The call has been ended good bye")
        except Exception as e:
            logger.error(f"End call error: {e}")
            await ctx.room.disconnect()
            
        return "The call has ended. Goodbye."

    # --- Fetch Lead Data ---
    candidate_name = "Candidate"
    phone_no = "Unknown"
    
    if vici_unique_id:
        asyncio.create_task(start_recording(ctx.room.name, vici_unique_id))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://192.168.1.61:9001/get-data/{vici_unique_id}", timeout=2) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidate_name = data.get('field_1', "Candidate")
                        phone_no = data.get('field_3', "Unknown")
        except Exception as e:
            logger.error(f"Data Fetch Error: {e}")

    # --- Setup AI Pipeline ---
    all_steps = "".join([f"Step {i}: {r.hget(f'step:{i}', 'text')}\n" for i in range(1, 15) if r.hget(f"step:{i}", "text")])
    
    system_instructions = (
        "You are Kavya, an outbound recruitment assistant for Greet Technologies. "
        "Engage politely. Respond with numbers in words. \n"
        "If the user wants a human/agent, call transfer_to_agent. "
        "If the user is finished, call end_call.\n"
        "Flow: \n" + all_steps
    )

    # Register tools with the Agent
    agent = Agent(
        instructions=system_instructions,
        tools=[transfer_to_agent, end_call] 
    )

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"),
        tts=cartesia.TTS(model="sonic-english", voice="95d51f79-c397-46f9-b49a-23763d3eaa2d"),
    )

    # Ensure API client closes on session end
    ctx.add_shutdown_callback(lk_api.aclose)

    @session.on("conversation_item_added")
    def on_item_added(event: ConversationItemAddedEvent):
        if event.item.text_content:
            log_entry = {
                "vici_id": vici_unique_id or "Unknown",
                "phone_no": phone_no,
                "name": candidate_name,
                "role": event.item.role,
                "text": event.item.text_content,
                "room": ctx.room.name
            }
            asyncio.create_task(save_message_to_call(log_entry))

    await session.start(agent=agent, room=ctx.room)

    # Personalized Greet
    first_line = r.hget("step:1", "text") or "Hello?"
    personalized_line = first_line.replace("{{consumer_name}}", candidate_name)
    await session.generate_reply(instructions=f"Greet the user by saying exactly: {personalized_line}")
    
    @ctx.room.on("participant_disconnected")
    def on_disconnect(p):
        if vici_unique_id:
            asyncio.create_task(cleanup_call(vici_unique_id))

if __name__ == "__main__":
    cli.run_app(server)
