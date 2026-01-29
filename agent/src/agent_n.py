import redis
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
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
# 1. Initialize Redis (Connects to your Sandbox)
# decode_responses=True ensures we get back text (strings) not bytes
r = redis.Redis(host='localhost', port=6379, decode_responses=True)

server = AgentServer()
logger = logging.getLogger("livekit.agents")

@server.rtc_session()
async def entrypoint(ctx: JobContext):
    await ctx.connect() # Ensure connection to room
    
    # Wait for the SIP participant to join
    participant = await ctx.wait_for_participant()
    await asyncio.sleep(0.5)

    # Log all attributes to debug
    logger.info(f"All participant attributes: {participant.attributes}")
    
    # Try to get the X-VC-Payload header
    payload_encoded = None
    possible_keys = [
        "sip.header.X-VC-Payload",
        "sip.header.x-vc-payload",
        "X-VC-Payload",
        "x-vc-payload",
        "vcPayload"
    ]
    
    for key in possible_keys:
        if key in participant.attributes:
            payload_encoded = participant.attributes[key]
            logger.info(f"✓ Found payload at key: {key}")
            break
    
    # Decode the payload if found
    candidate_name = "Guest"  # Default
    vici_unique_id = participant.attributes.get("sip.phoneNumber")
    
    if payload_encoded:
        try:
            # Decode base64
            payload_json = base64.b64decode(payload_encoded).decode('utf-8')
            payload_data = json.loads(payload_json)
            
            logger.info(f"✓ Successfully decoded payload: {payload_data}")
            
            # Extract candidate name from payload
            candidate_name = payload_data.get('fn', 'Guest')
            logger.info(f"✓ Candidate name from payload: {candidate_name}")
            
        except Exception as e:
            logger.error(f"✗ Error decoding payload: {e}")
            logger.error(f"Raw payload value: {payload_encoded}")
    else:
        logger.warning("✗ X-VC-Payload header not found in attributes")
        logger.info(f"Available keys: {list(participant.attributes.keys())}")
    
    logger.info(f"--- VICI UNIQUE ID: {vici_unique_id} ---")
    logger.info(f"--- CANDIDATE NAME: {candidate_name} ---")
    
    # 2. Fetch the FIRST STEP from Redis to start the call
    start_step = r.hgetall("step:1")
    first_line = start_step.get("text", "Hello?")
    
    # System instructions
    system_instructions = (
        "You are Kavya, an outbound recruitment assistant for Greet Technologies. "
        "Engage politely and professionally. Always respond with numbers in words. "
        "Here is your primary recruitment script flow: \n"
    )

    # Fetch all steps from Redis
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

    # Start the call with personalized greeting
    personalized_line = first_line.replace("{{consumer_name}}", candidate_name)
    
    await session.generate_reply(instructions=f"Start the call by saying: {personalized_line}")

if __name__ == "__main__":
    cli.run_app(server)