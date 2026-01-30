import redis
import aiohttp
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    cli,
)
from livekit.plugins import silero, deepgram, openai, cartesia
from dotenv import load_dotenv
from pathlib import Path
import logging
import asyncio
import base64
import json

load_dotenv()

print(Path.cwd())

r = redis.Redis(host='localhost', port=6379, decode_responses=True)
server = AgentServer()
logger = logging.getLogger("livekit.agents")

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
        # Refresh participant data
        participant = ctx.room.participants.get(participant.sid)

    logger.info(f"--- FINAL VICI ID: {vici_unique_id} ---")
    logger.info(f"--- ALL ATTRS: {participant.attributes} ---")
    
    # Default values
    candidate_name = ""
    lead_id = ""
    
    # Fetch data from FastAPI endpoint
    if vici_unique_id:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://192.168.1.61:9001/get-data/{vici_unique_id}", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidate_name = data.get('field_1', '')
                        lead_id = data.get('field_2', '')
                        logger.info(f"✓ Successfully fetched data from API")
                        logger.info(f"✓ Candidate name: {candidate_name}")
                        logger.info(f"✓ Lead ID: {lead_id}")
                    else:
                        logger.warning(f"API returned status {resp.status}")
        except Exception as e:
            logger.error(f"Error fetching data from API: {e}")
    
    # Fetch Redis script steps
    start_step = r.hgetall("step:1")
    first_line = start_step.get("text", "Hello?")
    
    system_instructions = (
        "You are Kavya, an outbound recruitment assistant for Greet Technologies. "
        "Engage politely and professionally. Always respond with numbers in words. "
        "Here is your primary recruitment script flow: \n"
    )

    all_steps = ""
    for i in range(1, 15):
        text = r.hget(f"step:{i}", "text")
        if text:
            all_steps += f"Step {i}: {text}\n"

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"),
        tts=cartesia.TTS(
            model="sonic-english",
            voice="95d51f79-c397-46f9-b49a-23763d3eaa2d"
        ),
    )

    agent = Agent(
        instructions=system_instructions + all_steps,
    )

    await session.start(agent=agent, room=ctx.room)

    # Start with personalized greeting
    personalized_line = first_line.replace("{{consumer_name}}", candidate_name)
    await session.generate_reply(instructions=f"Start the call by saying: {personalized_line}")
    
    # Optional: Clean up data after call
    @ctx.room.on("participant_disconnected")
    def on_disconnect(participant):
        asyncio.create_task(cleanup_data(vici_unique_id))
    
async def cleanup_data(unique_id):
    """Clean up call data after the call ends"""
    try:
        async with aiohttp.ClientSession() as session:
            await session.delete(f"http://192.168.1.61:9001/clear-data/{unique_id}")
            logger.info(f"Cleaned up data for {unique_id}")
    except Exception as e:
        logger.error(f"Error cleaning up data: {e}")

if __name__ == "__main__":
    cli.run_app(server)
