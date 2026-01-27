### **Quick Start Guide**

1. **Create a virtual environment:** **`python3 -m venv .venv`** or **`uv venv`**

2. **Activate the environment:** **`source .venv/bin/activate`**

3. **Initiate the project:** **`uv init`**

4. **Download/Sync dependencies from pyproject.toml:** **`uv sync`**

5. **Install a new package:** **`uv add [package_name]`**

6. **Run the agent in development mode:** **`uv run agent.py dev`**

7. **To kill agent**: `sudo fuser -k 8081/tcp`

8. **Find what's using port 8081**
``
sudo lsof -i :8081
sudo netstat -tulpn | grep :8081
sudo ss -tulpn | grep :8081
``



[ai]
exten => 9000,1,NoOp(Call to LiveKit Agent)
 ;same => n,Dial(PJSIP/livekit-endpoint/sip:agent@192.168.1.61,30)
 same => n,Set(VENDOR_LEAD_CODE=${CALLERID(name)})
 same => n,AGI(agi-test.pl,${VENDOR_LEAD_CODE},60)  ; 60 = max wait seconds
 same => n,Set(PJSIP_HEADER(add,X-Candidate-Name)=${LEAD_FIRST_NAME})
 same => n,Dial(PJSIP/agent@livekit-endpoint,30)
 same => n,Hangup()

