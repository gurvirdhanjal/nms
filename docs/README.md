# Device Monitoring Tactical

## Overview

**Device Monitoring Tactical** is a comprehensive network and device monitoring system designed to provide real-time visibility into your infrastructure. It supports agent-based monitoring for servers and agentless monitoring via SNMP and WMI for network devices. The system includes a robust dashboard for visualization, alerting capabilities, and detailed reporting.

## Key Features

- **Real-time Monitoring**: Track CPU, Memory, Disk, and Network usage in real-time.
- **Multi-Protocol Support**:
  - **Agent-based**: Python agent (`server_agent.py`) for deep server metrics.
  - **SNMP**: Monitor network devices (routers, switches) using SNMP v1/v2c/v3.
  - **WMI**: Windows Management Instrumentation support.
  - **ICMP/Ping**: Basic reachability checks.
- **Dashboard**: Interactive web interface built with Flask and Bootstrap 5.
- **Alerting**: Configurable alerts for critical thresholds (CPU, Memory, Disk, Offline status).
- **Reporting**: Generate operational and executive reports (HTML, CSV, Excel).
- **Network Discovery**: scan subnets to discover new devices.
- **Role-Based Access Control**: User management with Admin and User roles.

## Installation

### Prerequisites

- Python 3.9+
- PostgreSQL (Production) or SQLite (Development)
- Docker & Docker Compose (Optional, for containerized deployment)

### Local Setup

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd device-monitoring-tactical
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python -m venv venv
    # On Windows:
    venv\Scripts\activate
    # On macOS/Linux:
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configuration:**
    - The application uses `config.py` which loads settings from environment variables.
    - Create a `.env` file in the root directory (optional, defaults are provided for dev).
    - Key variables:
        - `SECRET_KEY`: Flask secret key.
        - `SQLALCHEMY_DATABASE_URI`: Database connection string (default: SQLite `instance/device_monitoring.db`).
        - `FLASK_DEBUG`: Set to `True` for development.

### Docker Setup

1.  **Build and run using Docker Compose:**
    ```bash
    docker-compose up --build -d
    ```
    This will start the web application and a PostgreSQL database.

## Usage

### Running the Server

To start the monitoring server:

```bash
python app.py
```

- The server will start on `http://0.0.0.0:5001`.
- **Default Admin Credentials:**
    - Username: `admin`
    - Password: `admin123`

### Running the Agent

To monitor a server, run the agent script on the target machine:

```bash
python server_agent.py
```

- Ensure the agent can reach the server URL defined in `server_agent.py` (`NMS_SERVER_URL`).
- You may need to update `NMS_SERVER_URL` in `server_agent.py` if the server is not on `localhost`.

### Accessing the Dashboard

Open your browser and navigate to `http://localhost:5001`. Log in with the default credentials to view the dashboard.

## Project Structure

```
.
├── app.py                  # Main Flask application entry point
├── config.py               # Configuration settings
├── models/                 # SQLAlchemy database models
│   ├── device.py           # Device model
│   ├── server_health.py    # Server health metrics
│   └── ...
├── routes/                 # Flask Blueprints (API endpoints)
│   ├── auth.py             # Authentication routes
│   ├── monitoring.py       # Monitoring API
│   ├── reports.py          # Reporting routes
│   └── ...
├── services/               # Business logic and background services
│   ├── scheduler.py        # Task scheduler
│   ├── snmp_service.py     # SNMP interaction logic
│   └── ...
├── static/                 # Static assets (CSS, JS, images)
├── templates/              # Jinja2 HTML templates
├── workers/                # Background worker processes
│   └── snmp_worker.py      # Dedicated SNMP polling worker
├── requirements.txt        # Python dependencies
└── README.md               # Project documentation
```

## Dependencies

Key dependencies include:

- **Flask**: Web framework.
- **Flask-SQLAlchemy**: ORM for database interactions.
- **Flask-Bcrypt**: Password hashing.
- **Schedule**: In-process task scheduling.
- **Pysnmp**: SNMP library.
- **Psutil**: System monitoring (used by the agent).
- **Requests**: HTTP library.
- **OpenCV**: Image processing (if applicable).

## Testing and Quality Gates

### Python test dependencies
```bash
pip install -r requirements-dev.txt
```

### JavaScript test dependencies
```bash
npm install
```

### Test commands
```bash
pytest -m "unit or integration"
pytest -m performance
npm run test:js
npm run test:js:coverage
npm run test:js:perf
python scripts/run_quality_gate.py
```

### Coverage thresholds
- Python touched backend modules: `>=95%`
- JS touched/new console modules: `>=95%`

## Contributing

Please refer to `AGENTS.md` and `CONVENTIONS.md` for coding standards and contribution guidelines.

## Agentic Skills

Standards-oriented skills for coding agents are indexed in `docs/skills.md`.

## RBAC Dashboard Validation Commands

Run all required checks locally:

```bash
python -m pytest
npm run test:js
npm run test:js:coverage
npm run test:js:perf
python scripts/run_quality_gate.py
```

These checks validate dashboard scope filtering, snapshot meta echo contracts, one-time RBAC mismatch refresh guard, and Files UI removal from device live console.
