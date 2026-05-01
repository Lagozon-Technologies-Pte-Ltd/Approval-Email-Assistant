from config import settings

print("----- ENV CHECK -----")
print("CLIENT ID:", settings.MICROSOFT_CLIENT_ID)
print("CLIENT SECRET:", settings.MICROSOFT_CLIENT_SECRET[:5] + "..." if settings.MICROSOFT_CLIENT_SECRET else "EMPTY")
print("REDIRECT URI:", settings.MICROSOFT_REDIRECT_URI)
print("TENANT ID:", settings.MICROSOFT_TENANT_ID)
print("---------------------")