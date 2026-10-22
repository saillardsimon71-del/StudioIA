"""Service d'images Meta AI isolé pour Studio IA V2.

Aucune dépendance à un fork ou à un paquet modifié : Playwright pilote directement
Meta AI et le service ne renvoie que des images prouvées du tour de conversation
courant.
"""

from .dom import ProvenanceError, RefusalError
from .service import SERVICE_VERSION, MetaAIImageService

__all__ = ["MetaAIImageService", "ProvenanceError", "RefusalError", "SERVICE_VERSION"]
__version__ = SERVICE_VERSION
