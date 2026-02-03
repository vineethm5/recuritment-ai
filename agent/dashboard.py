from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from motor.motor_asyncio import AsyncIOMotorClient
import os

app = FastAPI()

# MongoDB Setup
MONGO_URL = os.getenv("MONGO_URL", "mongodb://admin:secretpassword@localhost:27017/?authSource=admin")
client = AsyncIOMotorClient(MONGO_URL)
db = client.asterisk
collection = db.conversation_history

@app.get("/api/calls")
async def get_calls():
    # Fetch all calls, sorted by most recent
    cursor = collection.find().sort("created_at", -1)
    calls = await cursor.to_list(length=100)
    for call in calls:
        call["_id"] = str(call["_id"]) # Convert ObjectId to string for JSON
    return calls

@app.get("/api/call/{call_id}")
async def get_call_detail(call_id: str):
    call = await collection.find_one({"call_id": call_id})
    if call:
        call["_id"] = str(call["_id"])
    return call

# Simple Dashboard HTML


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
    <html>
        <head>
            <title>Kavya AI | Recruitment Dashboard</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            <style>
                body { background: #f4f7f6; height: 100vh; display: flex; flex-direction: column; }
                .main-container { display: flex; flex: 1; overflow: hidden; }
                #list { width: 350px; background: white; border-right: 1px solid #ddd; overflow-y: auto; }
                #detail { flex: 1; padding: 30px; overflow-y: auto; display: flex; flex-direction: column; }
                
                /* Chat Bubble Styling */
                .chat-container { display: flex; flex-direction: column; gap: 10px; padding: 20px; background: #fff; border-radius: 8px; border: 1px solid #eee; }
                .bubble { max-width: 75%; padding: 12px 16px; border-radius: 18px; position: relative; font-size: 14px; line-height: 1.4; }
                .agent { align-self: flex-start; background: #e9ecef; color: #333; border-bottom-left-radius: 2px; }
                .user { align-self: flex-end; background: #007bff; color: white; border-bottom-right-radius: 2px; }
                
                .eval-card { background: #fff3cd; border-left: 5px solid #ffc107; padding: 15px; margin-bottom: 20px; border-radius: 4px; }
                .call-item { cursor: pointer; border-bottom: 1px solid #f0f0f0; transition: 0.2s; }
                .call-item:hover { background: #f8f9fa; }
                .active-call { border-left: 5px solid #007bff; background: #eef6ff !important; }
            </style>
        </head>
        <body>
            <div class="bg-dark text-white p-3 shadow-sm">
                <h5 class="m-0">Kavya AI Recruitment Portal</h5>
            </div>
            
            <div class="main-container">
                <div id="list" class="list-group list-group-flush">
                    <div class="p-3 bg-light border-bottom"><strong>Recent Calls</strong></div>
                    <div id="call-list"></div>
                </div>

                <div id="detail">
                    <div id="call-content">
                        <div class="text-center mt-5 text-muted">
                            <h3>Select a candidate to view the interview</h3>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                async function loadCalls() {
                    const res = await fetch('/api/calls');
                    const calls = await res.json();
                    document.getElementById('call-list').innerHTML = calls.map(c => `
                        <div class="call-item list-group-item p-3" onclick="loadDetail('${c.call_id}', this)">
                            <div class="d-flex justify-content-between">
                                <strong>${c.name || 'Unknown'}</strong>
                                <span class="badge ${c.status === 'completed' ? 'bg-success' : 'bg-primary'}">${c.status}</span>
                            </div>
                            <small class="text-muted">${c.phone_no || ''}</small>
                        </div>
                    `).join('');
                }

                async function loadDetail(id, element) {
                    // Highlight selected item
                    document.querySelectorAll('.call-item').forEach(el => el.classList.remove('active-call'));
                    element.classList.add('active-call');

                    const res = await fetch(`/api/call/${id}`);
                    const c = await res.json();
                    
                    // Create chat bubbles
                    let transcriptHtml = c.messages.map(m => `
                        <div class="bubble ${m.role === 'agent' ? 'agent' : 'user'}">
                            <strong>${m.role.toUpperCase()}</strong><br>${m.text}
                        </div>
                    `).join('');
                    
                    document.getElementById('call-content').innerHTML = `
                        <div class="d-flex justify-content-between align-items-center mb-4">
                            <h2>${c.name || 'Candidate'} Detail</h2>
                            <audio controls src="/recordings/${c.call_id}.mp3"></audio>
                        </div>

                        <div class="eval-card shadow-sm">
                            <h6 class="text-warning-emphasis">🧠 AI Evaluation</h6>
                            <p class="mb-0">${c.audio_prosody_evaluation || '<i>Analysis pending...</i>'}</p>
                        </div>

                        <h4>Conversation Transcript</h4>
                        <div class="chat-container shadow-sm">${transcriptHtml}</div>
                    `;
                }

                loadCalls();
                setInterval(loadCalls, 10000); 
            </script>
        </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9002)