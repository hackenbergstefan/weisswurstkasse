class PrivatePagesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path != "/health/":
            response["Cache-Control"] = "private, no-store"
            response["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; font-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'"
            )
        return response
