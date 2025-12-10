import requests

# URL of your running Llama server
SERVER_URL = "http://127.0.0.1:5000/v1/completions"

def query_model(prompt: str, max_tokens: int = 100):
    """
    Send a prompt to the Llama server and return the response text.
    """
    payload = {
        "model": "senecallm-q4_k_m.gguf",  # your model filename
        "prompt": prompt,
        "max_tokens": max_tokens
    }

    try:
        response = requests.post(SERVER_URL, json=payload)
        response.raise_for_status()
        result = response.json()
        return result['choices'][0]['text']
    except requests.exceptions.RequestException as e:
        return f"Error communicating with server: {e}"

if __name__ == "__main__":
    print("✅ Connected to Llama server. Type 'exit' to quit.\n")
    while True:
        user_prompt = input("You: ")
        if user_prompt.lower() in ["exit", "quit"]:
            print("Exiting chat. Goodbye!")
            break
        output = query_model(user_prompt)
        print("Model:", output, "\n")
