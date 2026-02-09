import redis

rd = redis.Redis(host='localhost', port=6379, decode_responses=True)

recruitment_steps = {
    "1": {"text": "Hi, may I speak with {{consumer_name}}?", "next": "2"},
    "2": {"text": "This is {{recruiter_role}} from Greet Technologies. I found your profile on Naukri—are you currently exploring job opportunities?", "next": "3"},
    "3": {"text": "We're hiring for an Accounts Process Executive role. Would you like to know more?", "next": "4"},
    "4": {"text": "We are located in HSR Layout. Would it be convenient for you to commute to this location for work?", "next": "5"},
    "5": {
        "text": "What are all languages you can speak?", 
        "next": "6",
        "logic": "check_hindi" # Special flag for your Agent code
    },
    "hindi_fail": {
        "text": "I understand. Hindi is a mandatory requirement for this position. Unfortunately, we cannot proceed, but we will keep your profile in our database. Goodbye.",
        "next": "end"
    },
    "6": {
        "text": "Could you tell me a bit about yourself in Hindi and English?", 
        "next": "7",
        "logic": "evaluate_language" # Trigger for scoring 6/10 Hindi, 7/10 English
    },
    "eval_fail": {
        "text": "Thank you for your introduction. Unfortunately, your proficiency levels do not meet the minimum requirement for this role. We appreciate your time. Goodbye.",
        "next": "end"
    },
    "7": {"text": "This role involves working on Tally software. You'd be helping CA and CS clients with their accounting queries. How does that sound?", "next": "8"},
    "8": {"text": "There are no sales targets or agreements—it's a stable KPO process. Are you comfortable with this kind of work?", "next": "9"},
    "9": {"text": "Since this is a specialized role, there's a thirty to thirty-five day training covering Tally, TDS, and GST—with certification. Does that work for you?", "next": "10"},
    "10": {"text": "During training, you'll receive a stipend of ten thousand five hundred rupees. Is that okay?", "next": "11"},
    "11": {"text": "After training, the CTC is twenty thousand two hundred rupees. Take-home would be around eighteen thousand six hundred without PF, or fifteen thousand with PF. Any questions on this?", "next": "12"},
    "12": {"text": "Great! Would you be available for an interview tomorrow?", "next": "13"},
    "13": {"text": "Could you share your updated resume on this WhatsApp number?", "next": "14"},
    "14": {"text": "Once your profile is shortlisted, I'll send you the interview location and details. Sound good?", "next": "end"}
}

pipe = rd.pipeline()
for step_id, data in recruitment_steps.items():
    pipe.hset(f"step:{step_id}", mapping=data)
pipe.execute()
print("🚀 Full Kavya Recruitment Flow Loaded!")