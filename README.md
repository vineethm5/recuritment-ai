# H1 Instruction To Follow 
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
