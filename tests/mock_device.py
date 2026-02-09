from flask import Flask, jsonify
import time
import random

app = Flask(__name__)
START_TIME = time.time()

@app.route("/health")
def health():
    uptime = int(time.time() - START_TIME)

    return jsonify({
        "status": "ok",
        "cpu": random.randint(5, 70),
        "memory": random.randint(20, 80),
        "uptime": uptime
    })


if __name__ == "__main__":
    print("Mock device API running on http://127.0.0.1:9000")
    app.run(host="0.0.0.0", port=9000)
