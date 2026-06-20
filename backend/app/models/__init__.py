"""Import all models so SQLAlchemy metadata is populated"""
from app.models.user import User
from app.models.change_request import ChangeRequest, ServerTask
from app.models.agent_run import AgentRun, AgentLog
from app.models.knowledge import DependencyEdge, ScheduledRebootWindow, ServicePauseConfig, ServerKBDocument
from app.models.server import Server
from app.models.incident import Incident

all_models = [User, ChangeRequest, ServerTask, AgentRun, AgentLog,
              DependencyEdge, ScheduledRebootWindow, ServicePauseConfig,
              Server, Incident]
