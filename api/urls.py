from rest_framework.routers import DefaultRouter
from .views import (
    UserViewSet, WorkspaceViewSet, DocumentViewSet,
    CommentViewSet, TagViewSet, AuditLogViewSet
)

router = DefaultRouter()

# Each register() call creates the full set of URLs for that ViewSet.
# The router reads which HTTP methods the ViewSet supports and
# automatically wires list (no pk) and detail (with pk) routes.
# @action decorators get their own URLs automatically too.

router.register(r'users', UserViewSet, basename='user')
router.register(r'workspaces', WorkspaceViewSet, basename='workspace')
router.register(r'documents', DocumentViewSet, basename='document')
router.register(r'comments', CommentViewSet, basename='comment')
router.register(r'tags', TagViewSet, basename='tag')
router.register(r'audit-logs', AuditLogViewSet, basename='auditlog')

urlpatterns = router.urls