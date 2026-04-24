import re

with open("routes/agent.py", "r", encoding="utf-8") as f:
    text = f.read()

old_code = '''        try:
            db.session.commit()
        except (IntegrityError, StaleDataError) as commit_err:'''

new_code = '''        agent_latency_ms = data.get('agent_latency_ms')
        if agent_latency_ms is not None:
            try:
                from models.scan_history import DeviceScanHistory
                scan = DeviceScanHistory(
                    device_ip=device.device_ip,
                    device_name=device.device_name,
                    status='Online',
                    status_detail='Metrics reported',
                    ping_time_ms=float(agent_latency_ms),
                    packet_loss=0,
                    scan_timestamp=datetime.utcnow(),
                    scan_type='agent_push'
                )
                db.session.add(scan)
            except Exception as e:
                print(f"[Agent] Failed to record latency history: {e}")

        try:
            db.session.commit()
        except (IntegrityError, StaleDataError) as commit_err:'''

if old_code in text:
    text = text.replace(old_code, new_code)
    with open("routes/agent.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("routes/agent.py successfully patched")
else:
    print("Target code not found in routes/agent.py")
