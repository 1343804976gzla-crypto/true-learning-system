"""
服务模块
AI相关服务
"""

from services.ai_client import get_ai_client, AIClient
from services.content_parser import get_content_parser, ContentParser
from services.quiz_service import get_quiz_service, QuizService
from services.feynman_service import get_feynman_service, FeynmanService
from services.variation_service import get_variation_service, VariationService

__all__ = [
    'get_ai_client', 'AIClient',
    'get_content_parser', 'ContentParser',
    'get_quiz_service', 'QuizService',
    'get_feynman_service', 'FeynmanService',
    'get_variation_service', 'VariationService',
]
