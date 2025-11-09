# Will probably need an always-on server for effective chatting
# Test comment for diff view

import sys
import os
import traceback
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import logging
from app.endpoints import chat
from uuid import uuid4
import asyncio
import threading

# Configure structured logging
from app.utils.logging.logging_config import setup_logging_from_env
from app.utils.logging.component_loggers import get_api_logger, log_api_event

# Import cache service for middleware
from app.services.request_cache import RequestCacheService

# Initialize structured logging (this creates the log file automatically)
setup_logging_from_env()
logger = get_api_logger(__name__)

app = FastAPI(
    title="Juniper API",
    description="API for Juniper application",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

@app.middleware("http")
async def cache_cleanup_middleware(request: Request, call_next):
    """
    CRITICAL: Middleware for automatic cache cleanup after every request.
    This prevents memory leaks and ensures cache isolation between requests.
    Runs even if request fails or raises an exception.
    """
    # Set request_id in request.state for consistency across the app
    # Note: Frontend request_id will be extracted and set in the endpoint
    request_id = getattr(request.state, 'request_id', None)
    if not request_id:
        # Generate request_id if not already set (will be overridden by frontend ID if provided)
        request_id = str(uuid4())
        request.state.request_id = request_id
    
    try:
        # Process the request
        response = await call_next(request)
        return response
    except Exception as e:
        # Re-raise the exception after cleanup
        raise
    finally:
        # CRITICAL: Always cleanup cache, even on errors
        try:
            RequestCacheService.cleanup_request(request_id)
            log_api_event(
                logger,
                f"Cache cleaned up for request_id: {request_id}",
                request_id=request_id,
                action="cache_cleanup_success"
            )
        except Exception as cleanup_error:
            log_api_event(
                logger,
                f"Failed to cleanup cache for request_id: {request_id} - {str(cleanup_error)}",
                level="ERROR",
                request_id=request_id,
                action="cache_cleanup_error",
                error_message=str(cleanup_error)
)

@app.middleware("http")
async def error_logging_middleware(request: Request, call_next):
    import time
    start_time = time.time()
    
    # Log the Origin header for debugging
    origin = request.headers.get("origin", "No Origin header")
    user_agent = request.headers.get("user-agent", "No User-Agent")
    logger.info(f"Incoming request - Origin: {origin}, User-Agent: {user_agent}, Path: {request.url.path}")
    
    try:
        response = await call_next(request)
        duration = time.time() - start_time
        
        # Log successful request
        log_api_event(
            logger,
            f"Request completed: {request.method} {request.url.path}",
            endpoint=str(request.url.path),
            method=request.method,
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 2),
            request_id=getattr(request.state, 'request_id', None)
        )
        
        return response
    except Exception as e:
        duration = time.time() - start_time
        
        # Log error with structured data
        log_api_event(
            logger,
            f"Request failed: {request.method} {request.url.path} - {str(e)}",
            level="ERROR",
            endpoint=str(request.url.path),
            method=request.method,
            status_code=500,
            duration_ms=round(duration * 1000, 2),
            error_type=type(e).__name__,
            error_message=str(e),
            request_id=getattr(request.state, 'request_id', None)
        )
        
        return JSONResponse(
            status_code=500,
            content={
                "detail": str(e),
                "traceback": traceback.format_exc()
            }
        )

# Configure CORS - Allow all origins since we use authentication
# Authentication is handled by get_current_user dependency in endpoints
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins - security via authentication
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/")
async def root():
    return {"message": "Juniper API"}

app.include_router(chat.router)

def run_server(port: int):
    """Run a single server instance on the specified port"""
    config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,  # Disable reload for multiple instances
        log_level="info"
    )
    server = uvicorn.Server(config)
    server.run()

def run_multiple_servers():
    """Run servers on ports 8000, 8001, and 8002"""
    ports = [8000, 8001, 8002]
    threads = []
    
    logger.info(f"Starting servers on ports: {ports}")
    
    for port in ports:
        thread = threading.Thread(target=run_server, args=(port,), daemon=True)
        thread.start()
        threads.append(thread)
        logger.info(f"Started server on port {port}")
    
    # Wait for all threads to complete (they won't unless interrupted)
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logger.info("Shutting down all servers...")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run Juniper API server(s)")
    parser.add_argument("--port", type=int, help="Single port to run on")
    parser.add_argument("--multi", action="store_true", help="Run on multiple ports (8000, 8001, 8002)")
    
    args = parser.parse_args()
    
    if args.multi:
        # Run multiple servers
        run_multiple_servers()
    elif args.port:
        # Run on specific port
        port = args.port
        logger.info(f"Starting server on port {port}")
        uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
    else:
        # Default: run on port 8000
        port = int(os.getenv("PORT", 8000))
        logger.info(f"Starting server on port {port}")
        uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)


#python -m app.main
#python -m app.main --port 8005
#python -m app.main --multi