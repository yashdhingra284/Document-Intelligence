from fastapi import FastAPI

app = FastAPI(title = "Document Intelligence API")

@app.get("/")
def home():
    return{
        "message":"Document Intelligence API is running"
    }