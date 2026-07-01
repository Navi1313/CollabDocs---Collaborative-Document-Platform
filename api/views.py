from django.contrib.auth import login, logout
from django.db import transaction, IntegrityError
from django.db.migrations import serializer
from django.db.models import Q, Count
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from api.models import (
    User, Workspace, WorkspaceMember, Tag,
    Document, DocumentVersion, Comment, AuditLog,
)
from api.serializers import (
    UserSerializer, RegisterSerializer, LoginSerializer,
    WorkspaceSerializer, WorkspaceMemberSerializer,
    TagSerializer, DocumentSerializer, DocumentVersionSerializer,
    CommentSerializer, AuditLogSerializer,
)
# -------------------------------------------------------------------------
# Users
# -------------------------------------------------------------------------
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    def get_permissions(self):
        if self.action in ['register', 'login_user']:
            return [AllowAny()]
        return [IsAuthenticated()]
    @action(detail=False, methods=['post'], url_path='register')
    def register(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)
    @action(detail=False, methods=['post'], url_path='login')
    def login_user(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        login(request, user)
        return Response(UserSerializer(user).data)
    @action(detail=False, methods=['post'], url_path='logout')
    def logout_user(self, request):
        logout(request)
        return Response({'message': 'Logged out successfully.'})
# -------------------------------------------------------------------------
# Workspaces
# -------------------------------------------------------------------------
class WorkspaceViewSet(viewsets.ModelViewSet):
    serializer_class = WorkspaceSerializer
    permission_classes = [IsAuthenticated]
    def get_queryset(self):
        # Only workspaces the user is a member of or owns
        return (
            Workspace.objects
            .filter(
                Q(owner=self.request.user) |
                Q(members__user=self.request.user)
            )
            .select_related('owner')
            .annotate(member_count=Count('members', distinct=True))
            .distinct()
        )
    def perform_create(self, serializer):
        with transaction.atomic():
            workspace = serializer.save(owner=self.request.user)
            WorkspaceMember.objects.create(
                workspace=workspace,
                user=self.request.user,
                role=WorkspaceMember.Role.ADMIN,
            )
    @action(detail=True, methods=['post'], url_path='add-member')
    def add_member(self, request, pk=None):
        workspace = self.get_object()
        serializer = WorkspaceMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = serializer.validated_data['user_id']
        role = serializer.validated_data.get('role', WorkspaceMember.Role.VIEWER)
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            with transaction.atomic():
                member = WorkspaceMember.objects.create(
                    workspace=workspace, user=user, role=role
                )
        except IntegrityError:
            return Response(
                {'error': 'User is already a member of this workspace.'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(WorkspaceMemberSerializer(member).data, status=status.HTTP_201_CREATED)
    @action(detail=True, methods=['get'], url_path='stats')
    def stats(self, request, pk=None):
        workspace = self.get_object()
        data = Workspace.objects.filter(pk=workspace.pk).annotate(
            total_documents=Count('documents', distinct=True),
            total_members=Count('members', distinct=True),
        ).values('total_documents', 'total_members').first()
        data['workspace_id'] = str(workspace.pk)
        data['name'] = workspace.name
        return Response(data)
# -------------------------------------------------------------------------
# Documents
# -------------------------------------------------------------------------
class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    permission_classes = [IsAuthenticated]
    def get_queryset(self):
        qs = (
            Document.objects
            .select_related('workspace', 'created_by')
            .prefetch_related('tags', 'versions')
        )
        # Filter by workspace
        workspace_id = self.request.query_params.get('workspace')
        if workspace_id:
            qs = qs.filter(workspace_id=workspace_id)
        # OR filter: search title or content
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(title__icontains=search) | Q(content__icontains=search)
            )
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        # Filter by tag name
        tag = self.request.query_params.get('tag')
        if tag:
            qs = qs.filter(tags__name__icontains=tag)
        return qs.distinct()
    def perform_create(self, serializer):
        with transaction.atomic():
            doc = serializer.save(created_by=self.request.user)
            version_number = doc.versions.count() + 1
            DocumentVersion.objects.create(
                document=doc,
                version_number=version_number,
                content=doc.content,
            )
            AuditLog.objects.create(
                actor=self.request.user,
                action='created',
                model_name='Document',
                object_id=doc.pk,
            )
    def perform_update(self, serializer):
        with transaction.atomic():
            doc = serializer.save()
            version_number = doc.versions.count() + 1
            DocumentVersion.objects.create(
                document=doc,
                version_number=version_number,
                content=doc.content,
            )
            AuditLog.objects.create(
                actor=self.request.user,
                action='updated',
                model_name='Document',
                object_id=doc.pk,
            )
    @action(detail=True, methods=['get'], url_path='versions')
    def versions(self, request, pk=None):
        doc = self.get_object()
        versions = doc.versions.all()
        return Response(DocumentVersionSerializer(versions, many=True).data)
    @action(detail=True, methods=['get'], url_path='summary')
    def summary(self, request, pk=None):
        doc = self.get_object()
        data = Document.objects.filter(pk=doc.pk).annotate(
            total_versions=Count('versions', distinct=True),
            total_comments=Count('comments', distinct=True),
        ).values('total_versions', 'total_comments').first()
        data['id'] = str(doc.pk)
        data['title'] = doc.title
        data['status'] = doc.status
        data['tag_names'] = list(doc.tags.values_list('name', flat=True))
        return Response(data)
    @action(detail=True, methods=['post'], url_path='tags/add')
    def add_tag(self, request, pk=None):
        doc = self.get_object()
        tag_id = request.data.get('tag_id')
        if not tag_id:
            return Response({'error': 'tag_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            tag = Tag.objects.get(pk=tag_id)
        except Tag.DoesNotExist:
            return Response({'error': 'Tag not found.'}, status=status.HTTP_404_NOT_FOUND)
        doc.tags.add(tag)
        return Response(DocumentSerializer(doc).data)
    @action(detail=True, methods=['post'], url_path='tags/remove')
    def remove_tag(self, request, pk=None):
        doc = self.get_object()
        tag_id = request.data.get('tag_id')
        if not tag_id:
            return Response({'error': 'tag_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            tag = Tag.objects.get(pk=tag_id)
        except Tag.DoesNotExist:
            return Response({'error': 'Tag not found.'}, status=status.HTTP_404_NOT_FOUND)
        doc.tags.remove(tag)
        return Response(DocumentSerializer(doc).data)
# -------------------------------------------------------------------------
# Comments
# -------------------------------------------------------------------------
class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer
    permission_classes = [IsAuthenticated]
    def get_queryset(self):
        qs = Comment.objects.select_related('user', 'document', 'parent')
        document_id = self.request.query_params.get('document')
        if document_id:
            qs = qs.filter(document_id=document_id)
        # Only top-level comments unless replies=true
        if self.request.query_params.get('replies') != 'true':
            qs = qs.filter(parent__isnull=True)
        return qs
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
# -------------------------------------------------------------------------
# Tags
# -------------------------------------------------------------------------
class TagViewSet(viewsets.ModelViewSet):
    queryset = Tag.objects.annotate(document_count=Count('documents')).all()
    serializer_class = TagSerializer
    permission_classes = [IsAuthenticated]
# -------------------------------------------------------------------------
# Audit Logs
# -------------------------------------------------------------------------
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated]
    def get_queryset(self):
        qs = AuditLog.objects.select_related('actor')
        model_name = self.request.query_params.get('model')
        if model_name:
            qs = qs.filter(model_name__icontains=model_name)
        action_param = self.request.query_params.get('action')
        if action_param:
            qs = qs.filter(action=action_param)
        actor_id = self.request.query_params.get('actor')
        if actor_id:
            qs = qs.filter(actor_id=actor_id)
        return qs
