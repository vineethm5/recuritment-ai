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
load_dotenv()

print(Path.cwd())
# 1. Initialize Redis (Connects to your Sandbox)
# decode_responses=True ensures we get back text (strings) not bytes
r = redis.Redis(host='localhost', port=6379, decode_responses=True)

server = AgentServer()

@server.rtc_session()
async def entrypoint(ctx: JobContext):
    # 2. Fetch the FIRST STEP from Redis to start the call
    # We use step:1 as our starting point
    start_step = r.hgetall("step:1")
    first_line = start_step.get("text", "Hello?")

    # 3. Pull the Full System Instructions from Redis or a central config
    # We include your recruitment guidelines here
    system_instructions = (
        "You are Kavya, an outbound recruitment assistant for Greet Technologies. "
        "Engage politely and professionally. Always respond with numbers in words. "
        "Here is your primary recruitment script flow: \n"
    )

    # Optional: Fetch all steps from Redis to give the LLM the 'Full Map'
    all_steps = ""
    for i in range(1, 15):
        text = r.hget(f"step:{i}", "text")
        if text:
            all_steps += f"Step {i}: {text}\n"

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"), # Recommended for better reasoning
        tts=cartesia.TTS(
            model="sonic-english",
            voice="95d51f79-c397-46f9-b49a-23763d3eaa2d"
        ),
    )

    agent = Agent(
        instructions=system_instructions + all_steps,
        # No more weather tools—the 'tool' is the Redis-backed script
    )

    await session.start(agent=agent, room=ctx.room)

    # 4. Start the call using the first line fetched from Redis
    # We use consumer_name if available in the metadata
    candidate_name = ctx.room.metadata or "Candidate"
    personalized_line = first_line.replace("{{consumer_name}}", candidate_name)
    
    await session.generate_reply(instructions=f"Start the call by saying: {personalized_line}")

if __name__ == "__main__":
    cli.run_app(server)
