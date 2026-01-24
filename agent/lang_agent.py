import asyncio
from livekit.agents import Agent, AgentServer, JobContext, cli, function_tool
from livekit.plugins import silero, deepgram, openai, cartesia
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
import redis

# Connect to your Redis
r = redis.Redis(host='localhost', port=6379, decode_responses=True)

class RecruitmentAssistant:
    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self.candidate_name = ctx.room.metadata or "Candidate"

    @function_tool
    async def evaluate_candidate(self, hindi_score: int, english_score: int):
        """
        Call this tool after the candidate introduces themselves in Step 6.
        It evaluates their language proficiency out of 10.
        """
        if hindi_score < 6 or english_score < 7:
            # logic to trigger rejection
            return "FAILED: Candidate does not meet language requirements. Politely end the call now."
        return "PASSED: Candidate is qualified. Proceed to Step 7."

    @function_tool
    async def end_call(self):
        """Call this tool only when the conversation is finished or candidate is rejected."""
        await self.ctx.room.disconnect()
        return "Call disconnected."

async def entrypoint(ctx: JobContext):
    assistant = RecruitmentAssistant(ctx)
    
    # Define the Brain with Tool access
    # We pass the evaluate_candidate tool so the LLM can make decisions
    agent = Agent(
        instructions=(
            "You are Kavya from Greet Technologies. Follow the 14-step script strictly. "
            "At Step 6, use the 'evaluate_candidate' tool to check Hindi (min 6/10) and English (min 7/10). "
            "If the tool returns 'FAILED', read the rejection script and then call 'end_call'. "
            "Always spell out numbers like 'Twenty thousand two hundred'."
        ),
        tools=[assistant.evaluate_candidate, assistant.end_call]
    )

    # Initialize Session (STT -> LLM -> TTS)
    # ... (Your standard silero, deepgram, cartesia setup here)
    
    await ctx.connect()
    # Start the call with Step 1 from Redis
    first_line = r.hget("step:1", "text").replace("{{consumer_name}}", assistant.candidate_name)
    await agent.say(first_line)

if __name__ == "__main__":
    cli.run_app(AgentServer(),entrypoint=entrypoint)
