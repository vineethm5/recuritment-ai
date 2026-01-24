import redis

rd = redis.Redis(host='localhost', port='6379', decode_responses=True )
# Define your Recruitment Flow
recruitment_steps = {
    "1": {
        "text": "Hi, may I speak with {{consumer_name}}?",
        "next": "2"
    },
    "2": {
        "text": "This is Kavya from Greet Technologies. I found your profile on Naukri—are you currently exploring job opportunities?",
        "next": "3"
    },
    "3": {
        "text": "We're hiring for an Accounts Process Executive role. Would you like to know more?",
        "next": "4"
    },
    "4": {
        "text": "We are located in HSR Layout. Would it be convenient for you to commute to this location for work?",
        "next": "5"
    },
    "5": {
        "text": "What are all the languages you can speak?",
        "next": "6"
    },
    "10": {
        "text": "During training, you'll receive a stipend of ten thousand five hundred rupees. Is that okay?",
        "next": "11"
    },
    "11": {
        "text": "After training, the CTC is twenty thousand two hundred rupees. Take-home would be around eighteen thousand six hundred without PF, or fifteen thousand with PF. Any questions on this?",
        "next": "12"
    }
    # Note: I've included the key salary steps to show how we handle numbers as words.
}

# Upload to Redis
pipe = rd.pipeline() # Using a pipeline is faster
for step_id, data in recruitment_steps.items():
    pipe.hset(f"step:{step_id}", mapping=data)
pipe.execute()

print("Recruitment steps loaded into Redis successfully!")