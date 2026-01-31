import redis
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    cli,
    ConversationItemAddedEvent
)
from livekit.plugins import silero, deepgram, openai, cartesia
from dotenv import load_dotenv
import logging
import asyncio
import datetime
load_dotenv()

# MongoDB Setup
MONGO_URL = "mongodb://admin:secretpassword@localhost:27017/?authSource=admin"
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client.asterisk
transcript_collection = db.conversation_history

r = redis.Redis(host='localhost', port=6379, decode_responses=True)
server = AgentServer()
logger = logging.getLogger("livekit.agents")

# --- Helper Functions (Outside entrypoint) ---
# async def save_to_mongo(data):
#     """Async helper to save transcript to MongoDB"""
#     try:
#         await transcript_collection.insert_one(data)
#     except Exception as e:
#         logger.error(f"❌ Mongo Save Error: {e}")


async def save_message_to_call(data):
    # Now 'data' is the whole dictionary
    filter_query = {"call_id": data["vici_id"]} 
    
    update_data = {
        "$push": {
            "messages": {
                "role": data["role"],
                "text": data["text"],
                "timestamp": datetime.datetime.utcnow()
            }
        },
        "$setOnInsert": {
            "created_at": datetime.datetime.utcnow(),
            "name":data["name"],
            "phone_no":data["phone_no"],
            "room": data["room"], # Optional: save the room name too
            "status": "active",
            
        }
    }

    await transcript_collection.update_one(filter_query, update_data, upsert=True)


    
async def cleanup_data(unique_id):
    """Clean up call data after the call ends"""
    try:
        async with aiohttp.ClientSession() as session:
            await session.delete(f"http://192.168.1.61:9001/clear-data/{unique_id}")
            logger.info(f"Cleaned up data for {unique_id}")
    except Exception as e:
        logger.error(f"Error cleaning up data: {e}")

@server.rtc_session()
async def entrypoint(ctx: JobContext):
    await ctx.connect()
    
    participant = await ctx.wait_for_participant()
    vici_unique_id = None
    for _ in range(6): 
        vici_unique_id = participant.attributes.get("vici_id")
        if vici_unique_id:
            break
        await asyncio.sleep(0.5)
        participant = ctx.room.participants.get(participant.sid)

    # Data fetching logic
    candidate_name = "Candidate"
    lead_id = vici_unique_id or "Unknown"
    
    if vici_unique_id:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://192.168.1.61:9001/get-data/{vici_unique_id}", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidate_name = data.get('field_1')
                        lead_id = data.get('field_2')
                        phone_no = data.get('field_3')
        except Exception as e:
            logger.error(f"Error fetching data: {e}")

    # Script Preparation
    start_step = r.hgetall("step:1")
    first_line = start_step.get("text", "Hello?")
    all_steps = "".join([f"Step {i}: {r.hget(f'step:{i}', 'text')}\n" for i in range(1, 15) if r.hget(f"step:{i}", "text")])

    system_instructions = (
        "You are Kavya, an outbound recruitment assistant for Greet Technologies. "
        "Engage politely and professionally. Always respond with numbers in words. "
        "Here is your primary recruitment script flow: \n" + all_steps
    )

    # Define Agent and Session
    agent = Agent(instructions=system_instructions)
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"),
        tts=cartesia.TTS(model="sonic-english", voice="95d51f79-c397-46f9-b49a-23763d3eaa2d"),
    )

    # --- Conversation Logger Event ---
    @session.on("conversation_item_added")
    def on_item_added(event: ConversationItemAddedEvent):
        item = event.item
        if item.text_content:
            log_entry = {
                "vici_id": lead_id,
                "phone_no": phone_no,
                "name": candidate_name,
                "role": item.role,
                "text": item.text_content,
                "timestamp": asyncio.get_event_loop().time(),
                "room": ctx.room.name
            }
            asyncio.create_task(save_message_to_call(log_entry))
            logger.info(f"💾 Logged {item.role}: {item.text_content[:30]}...")

    # --- Start the session once ---
    await session.start(agent=agent, room=ctx.room)

    # Personalized Greeting
    personalized_line = first_line.replace("{{consumer_name}}", candidate_name)
    await session.generate_reply(instructions=f"Start the call by saying: {personalized_line}")
    
    @ctx.room.on("participant_disconnected")
    def on_disconnect(p):
        if vici_unique_id:
            asyncio.create_task(cleanup_data(vici_unique_id))

if __name__ == "__main__":
    cli.run_app(server)