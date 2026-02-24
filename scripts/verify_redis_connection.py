import redis

redis_url = "redis://default:D94fZxj196v9oyHgamcpauqdRK8cjtA9@redis-12227.c321.us-east-1-2.ec2.cloud.redislabs.com:12227"

try:
    print(f"Testing connection to: {redis_url}")
    client = redis.Redis.from_url(redis_url, socket_connect_timeout=5, decode_responses=True)
    
    # Test ping
    print("Sending PING...")
    response = client.ping()
    print(f"PING Response: {response}")
    
    # Test read/write
    print("Testing SET/GET...")
    client.set("test_key", "hello_redis", ex=10)
    val = client.get("test_key")
    print(f"GET Response: {val}")
    
    print("✅ Redis connection successful and fully functional!")
    
except Exception as e:
    print(f"❌ Redis connection failed: {e}")
