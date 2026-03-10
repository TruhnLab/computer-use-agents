#!/bin/bash
# Quick start script for Computer Use Agent Web App

echo "🚀 Starting Computer Use Agent Web App"
echo ""

# Check if virtual environment exists
if [ ! -d "visualagent" ]; then
    echo "❌ Virtual environment 'visualagent' not found!"
    echo "Please run: uv venv visualagent && source visualagent/bin/activate"
    exit 1
fi

# Install backend dependencies
echo "📦 Installing backend dependencies..."
cd backend
pip install -q -r requirements.txt
cd ..

# Check if Node is installed
if ! command -v npm &> /dev/null; then
    echo "❌ npm not found! Please install Node.js first."
    exit 1
fi

# Install frontend dependencies
echo "📦 Installing frontend dependencies..."
cd frontend
if [ ! -d "node_modules" ]; then
    npm install
fi
cd ..

echo ""
echo "✅ Setup complete!"
echo ""
echo "Starting servers..."
echo ""

# Start backend in background
echo "🔧 Starting Flask backend (port 5000)..."
cd backend
python app.py &
BACKEND_PID=$!
cd ..

# Wait for backend to start
sleep 2

# Start frontend
echo "⚛️  Starting React frontend (port 3000)..."
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✨ Web app is running!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🌐 Frontend: http://localhost:3000"
echo "🔧 Backend:  http://localhost:5000"
echo ""
echo "Press Ctrl+C to stop all servers"
echo ""

# Wait for Ctrl+C
trap "echo ''; echo 'Stopping servers...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT

# Keep script running
wait
