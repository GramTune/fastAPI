# main.py
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello from Render + FastAPI!"}

@app.get("/ping")
def ping():
    return {"pong": True}
