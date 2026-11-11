import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("BACKEND_PORT", "8010")))
