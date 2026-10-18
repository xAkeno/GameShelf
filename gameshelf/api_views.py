import os
from django.http import JsonResponse

# API_KEY = os.environ.get("GAMESHELF_API_KEY", "dev-test-key-123")

# def ping(request):
#     key = request.headers.get("X-API-Key")
#     if not key:
#         return JsonResponse({"error": "API key missing"}, status=401)
#     if key != API_KEY:
#         return JsonResponse({"error": "Invalid API key"}, status=403)
#     return JsonResponse({"status": "ok", "message": "API key accepted"})