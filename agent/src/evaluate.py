import json
import aiohttp
import asyncio
import base64
import os
import datetime
import logging
import re  # Added for robust JSON extraction
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from openai import OpenAI

load_dotenv()

# Vicidial API Config
VICIDIAL_API_URL = "http://192.168.1.63/vicidial/non_agent_api.php?source=test&user=6666&pass=Sap1260aps&function=add_lead&phone_code=91&list_id=995&dnc_check=N&campaign_dnc_check=Y&campaign_id=TESTCAMP&address1=&city=&state=&add_to_hopper=Y&hopper_local_call_time_check=Y"

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
    vici_id = doc.get("call_id")
    phone_no = doc.get("phone_no", "Unknown")
    candidate_name = doc.get("name", "Candidate")
    messages = doc.get("messages", [])
    file_path = f"/opt/greet/recordings/{vici_id}.mp3"

    if not os.path.exists(file_path):
        logger.warning(f"Recording not found for {vici_id}")
        return

    if not doc.get("messages") or not candidate_name:
        logger.warning(f"⚠️ Skipping {vici_id}: Missing required fields (name/messages).")
        await collection.update_one({"_id": doc["_id"]}, {"$set": {"evaluation_status": "skipped_missing_data"}})
        return

    if len(messages) < 4:  # This means "has at least 3 messages"
        logger.info(f"⏭️ Skipping {vici_id}: Conversation too short ({len(messages)} turns).")
        # Mark as evaluated so we don't keep checking it
        await collection.update_one({"_id": doc["_id"]}, {"$set": {"evaluation_status": "skipped_too_short"}})
        return

    try:
        logger.info(f"🧠 Analyzing full call for {vici_id} ({candidate_name})...")
        with open(file_path, "rb") as audio_file:
            audio_data = base64.b64encode(audio_file.read()).decode('utf-8')

        

        prompt = (
            "Analyze this recruitment call audio. The call may have disconnected early. "
            "Determine if the candidate showed genuine interest in the job during the time they were on the line. "
            "Return ONLY a JSON object: "
            "{"
            "'sentiment': 'positive/negative/neutral', "
            "'interest_level': 1-10, "
            "'call_outcome': 'completed_naturally/disconnected_early', "
            "'summary': 'brief summary', "
            "'recommendation': 'hire/no-hire'"
            "}"
        )

        response = openai_client.chat.completions.create(
            model="gpt-4o-audio-preview",
            modalities=["text"],
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {"data": audio_data, "format": "mp3"}}
            ]}]
        )

        # --- FIX 1: Robust JSON Parsing ---
        raw_eval = response.choices[0].message.content
        # Remove markdown code blocks if present
        json_match = re.search(r"\{.*\}", raw_eval, re.DOTALL)
        if json_match:
            eval_data = json.loads(json_match.group())
        else:
            raise ValueError(f"No JSON found in response: {raw_eval}")

        # --- FIX 2: Correct variable passing ---
        sentiment = eval_data.get("sentiment")
        interest = eval_data.get("interest_level", 0)

        is_hot_lead = interest >= 7 or (sentiment == "positive" and interest >= 5)

        if is_hot_lead:
            logger.info(f"✨ Hot Lead! Sending {candidate_name} to human agent.")
            # Pass candidate_name explicitly to the callback
            await trigger_vicidial_callback(phone_no, vici_id, candidate_name)

        await collection.update_one(
            {"_id": doc["_id"]},
            {"$set": {
                "status": "completed",
                "ai_summary": eval_data.get("summary"),
                "ai_key_points": eval_data.get("key_points"),
                "audio_prosody_evaluation": eval_data,
                "evaluation_status": "completed",
                "evaluated_at": datetime.datetime.utcnow()
            }}
        )
        logger.info(f"✅ Full processing complete for {vici_id}")

    except Exception as e:
        logger.error(f"❌ Error during evaluation: {e}")
        await collection.update_one(
            {"_id": doc["_id"]}, 
            {"$set": {"evaluation_status": "failed_error", "error_log": str(e)}}
        )

async def trigger_vicidial_callback(phone, call_id, name):
    """Sends to Vicidial comments and puts lead in the hopper"""
    params = {
        "phone_number": str(phone),
        "first_name": str(name),
        "comments": f"CallID: {call_id}"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(VICIDIAL_API_URL, params=params) as resp:
            text = await resp.text()
            logger.info(f"☎️ Vicidial callback triggered: {text}")

async def main():
    logger.info("🚀 Evaluator Worker started. Watching for completed calls...")
    while True:
        # Added missing comma in the query dictionary below
        cursor = collection.find({
            "status": "yet_to_evaluate",
            "call_id": {"$exists": True},
            "ready_for_eval":{"$eq": True},
            "name": {"$exists": True},
            "messages.4": {"$exists": True},# This means "has at least 3 messages"

        })
        
        async for doc in cursor:
            await evaluate_call(doc)
        
        await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())

