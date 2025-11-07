.PHONY: setup install dev test run lint format clean health

# Setup development environment
setup:
	python3 -m venv venv
	. venv/bin/activate && pip install --upgrade pip setuptools wheel
	. venv/bin/activate && pip install -r requirements.txt
	@echo "✅ Development environment ready!"
	@echo "💡 Run 'source venv/bin/activate' to activate the environment"

# Install/reinstall requirements
install:
	. venv/bin/activate && pip install -r requirements.txt

# Development server with reload
dev:
	. venv/bin/activate && python -m app.main --port 8000

# Run production server
run:
	. venv/bin/activate && python -m app.main

# Run tests
test:
	. venv/bin/activate && pytest tests/ -v

# Run specific test
test-chat:
	. venv/bin/activate && python tests/test_chat_endpoint.py -p "Hi" -t 60


# Lint code (would install ruff if needed)
lint:
	@echo "💡 Install ruff for linting: pip install ruff"
	@echo "💡 Then run: ruff check ."

# Format code (would install ruff if needed)  
format:
	@echo "💡 Install ruff for formatting: pip install ruff"
	@echo "💡 Then run: ruff format ."

# Performance testing
test-supabase:
	. venv/bin/activate && python tests/load_tests/test_supabase_performance.py

test-performance:
	. venv/bin/activate && python tests/load_tests/compare_old_vs_new.py

# Health checks
health:
	curl -f http://localhost:8000/health/detailed || echo "⚠️  Detailed health check failed"

health-simple:
	curl -f http://localhost:8000/health || echo "⚠️  Health check failed"

# Clean up
clean:
	rm -rf venv_old_backup __pycache__ .pytest_cache
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -delete

# Show helpful commands
help:
	@echo "🚀 Available commands:"
	@echo "  make setup     - Create clean development environment"
	@echo "  make install   - Install/update requirements"
	@echo "  make dev       - Run development server with reload"
	@echo "  make run       - Run production server"
	@echo "  make test      - Run all tests"
	@echo "  make test-chat - Run chat endpoint test"
	@echo "  make health    - Check if server is running"
	@echo "  make clean     - Clean up temporary files"