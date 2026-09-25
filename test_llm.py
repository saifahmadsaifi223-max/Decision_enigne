from dotenv import load_dotenv
load_dotenv()

from app.llm_client import call_llm_json

result = call_llm_json(
    system="Respond with strict JSON only: {\"test\": true}",
    user="hello"
)
print(result)