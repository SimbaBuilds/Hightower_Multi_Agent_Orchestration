from typing import Dict, Any, List, Callable, Optional, Literal
from pydantic import BaseModel


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    type: Literal["text", "image"]
    timestamp: int


class Action(BaseModel):
    """Represents an action that can be taken by the agent."""
    name: str
    description: str
    parameters: Dict[str, Dict[str, Any]]
    returns: str
    example: Optional[str] = None
    handler: Callable

    class Config:
        arbitrary_types_allowed = True 