"""Open-Dynamo: draw, simulate and analyse dynamical systems."""
from .kernel import Reducer, Sim, System
from .model import Model, ModelError, ModelSpec, build

__version__ = "0.1.0"
__all__ = ["Model", "ModelError", "ModelSpec", "Reducer", "Sim", "System", "build", "__version__"]
