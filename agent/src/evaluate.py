import asyncio
import base64
import os
import datetime
import logging
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from openai import OpenAI

load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("evaluator")

# MongoDB Setup
MONGO_URL = os.getenv("MONGO_URL", "mongodb://admin:secretpassword@localhost:27017/?authSource=admin")
client = AsyncIOMotorClient(MONGO_URL)
db = client.asterisk
collection = db.conversation_history

# OpenAI Setup
openai_client = OpenAI()

async def evaluate_call(doc):
    """Processes a single call recording"""
    vici_id = doc.get("call_id")
    file_path = f"/opt/greet/recordings/{vici_id}.mp3"

    if not os.path.exists(file_path):
        logger.warning(f"⚠️ File not found for {file_path}, skipping for now...")
        return

    try:
        logger.info(f"🧠 Evaluating audio for {vici_id}...")
        with open(file_path, "rb") as audio_file:
            audio_data = base64.b64encode(audio_file.read()).decode('utf-8')

        response = openai_client.chat.completions.create(
            model="gpt-4o-audio-preview",
            modalities=["text"],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Evaluate this recruitment call. Provide: 1. Sentiment, 2. Interest Level, 3. Recommendation (Hire/No-Hire)."},
                        {"type": "input_audio", "input_audio": {"data": audio_data, "format": "mp3"}}
                    ]
                }
            ]
        )

        evaluation = response.choices[0].message.content
        
        # Update Mongo: Set evaluation and mark as processed
        await collection.update_one(
            {"_id": doc["_id"]},
            {"$set": {
                "audio_prosody_evaluation": evaluation,
                "evaluation_status": "completed",
                "evaluated_at": datetime.datetime.utcnow()
            }}
        )
        logger.info(f"✅ Evaluation complete for {vici_id}")

    except Exception as e:
        logger.error(f"❌ Error evaluating {vici_id}: {e}")

async def main():
    logger.info("🚀 Evaluator Worker started. Watching for completed calls...")
    while True:
        # Find calls that are 'completed' but don't have an evaluation yet
        cursor = collection.find({
            "status": "completed",
            "evaluation_status": {"$ne": "completed"}
        })
        
        async for doc in cursor:
            await evaluate_call(doc)
        
        # Sleep for 10 seconds before checking again
        await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())