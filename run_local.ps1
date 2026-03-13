# to run, simply run `.\run_local.ps1` in terminal from root directory

Write-Host "Starting REBOOT local environment..."

# Ensure .env exists
if (!(Test-Path ".env")) {
    Write-Host "Creating .env from example..."
    Copy-Item .env.example .env
}

# Start Neo4j
Write-Host "Starting Neo4j container..."
docker compose up neo4j -d

# Create venv if needed
if (!(Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment..."
    python -m venv .venv
}

# Activate venv
Write-Host "Activating virtual environment..."
.\.venv\Scripts\Activate.ps1

# Install dependencies
Write-Host "Installing dependencies..."
pip install -r middleware/requirements.txt

# Start server
Write-Host "Starting REBOOT server..."
python -m uvicorn middleware.main:app --reload --port 8000