## Instruction To Follow 
1. **To kill agent**: `sudo fuser -k 8081/tcp`

2. **To Generate api secret**: `openssl rand -base64 32`

3. **To Create Inbound Trunk** :
```
lk sip inbound create \
  --name "inbound-calls" \
  --numbers "agent" \ 
```
4. **To view trunk** : `lk sip inbound list`

5. **To create dispatch rule**: 
```
lk sip dispatch create \
  --name "vicidial-dispatch" \
  --trunks "[add_trunk_id]" \
  --caller voice-call- ****
```
6. **To view rule**: `lk sip dispatch list`

7. **To view rooms**: `lk room list`


# ---------------------------------------
## Vicibox Configuration

**Add the below pjsip.conf**
```
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060
tos=ef

[livekit-endpoint]
type=endpoint
transport=transport-udp
context=from-livekit
disallow=all
allow=ulaw,alaw
aors=livekit-aor
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
from_user=agent
from_domain=192.168.1.61

[livekit-aor]
type=aor
contact=sip:192.168.1.61:5060
qualify_frequency=0

[livekit-identify]
type=identify
endpoint=livekit-endpoint
match=192.168.1.61

```

**And the extension.conf**
```
exten => 9000,1,NoOp(Call to LiveKit Agent)
 same => n,Dial(PJSIP/agent@livekit-endpoint,30)
 same => n,Hangup()

```


graph TD
    A[User Voice] -->|Audio Stream| B(VAD: Silero)
    B -->|Speech Detected| C(STT: Deepgram)
    C -->|Text/Transcription| D{Agent Brain: LLM}
    
    D -->|Decision: Needs Data| E[Tool/Function Call]
    E -->|Database/API Result| D
    
    D -->|Final Response| F(TTS: Cartesia/OpenAI)
    F -->|Audio Stream| G[User Speaker]

    subgraph "Latency Optimization"
    C
    D
    F
    end

    style D fill:#f9f,stroke:#333,stroke-width:2px
    style B fill:#bbf,stroke:#333