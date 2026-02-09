from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import pymysql
from contextlib import contextmanager

app = FastAPI()

DB_CONFIG = {
    "host": "192.168.1.63",
    "port": 3306,
    "user": "cron",
    "password": "1234",
    "db": "asterisk",
}

@contextmanager
def get_db_connection():
    # DictCursor is active here
    connection = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    try:
        yield connection
    finally:
        connection.close()

class CallData(BaseModel):
    unique_id: str
    field_1: str 
    field_2: str 
    field_3: str 

@app.post("/receive-data")
async def receive_data(data: CallData):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                sql = """
                    INSERT INTO ai_call_data (unique_id, first_name, field_2, field_3)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        first_name = VALUES(first_name),
                        field_2 = VALUES(field_2),
                        field_3 = VALUES(field_3)
                """
                cursor.execute(sql, (data.unique_id, data.field_1, data.field_2, data.field_3))
                conn.commit()
        return {"status": "success", "unique_id": data.unique_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get-data/{unique_id}")
async def get_data(unique_id: str):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT unique_id, first_name, field_2, field_3 FROM ai_call_data WHERE unique_id = %s",
                    (unique_id,)
                )
                result = cursor.fetchone()
                if result:
                    return {
                        "unique_id": result['unique_id'],
                        "field_1": result['first_name'],
                        "field_2": result['field_2'],
                        "field_3": result['field_3']
                    }
                else:
                    raise HTTPException(status_code=404, detail="Data not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/clear-data/{unique_id}")
async def clear_data(unique_id: str):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM ai_call_data WHERE unique_id = %s", (unique_id,))
                conn.commit()
                return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/liveagents")
async def getliveagents():
    try:
        # 1. Added () to call the connection function
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 2. Fixed the SQL syntax (added ' after CLOSER and ) after the list)
                query = """
                    SELECT user, conf_exten 
                    FROM vicidial_live_agents 
                    WHERE status IN ('READY', 'CLOSER') 
                    AND user != '1111' 
                    LIMIT 1
                """
                cursor.execute(query)
                row = cursor.fetchone()
                
                if not row:
                    return {"user": None, "ext": "No agents available"}
                
                return {
                    "user": row['user'],
                    "ext": row['conf_exten']
                }
                
    except Exception as e:
        logger.error(f"Database error: {e}")
        # Always return the error detail during debugging to see what's wrong
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9001)


