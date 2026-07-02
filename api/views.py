from django.shortcuts import render

from django.db import transaction, IntegrityError
from django.db.models import Count, Q
from rest_framework import serializers, viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    User, Workspace, WorkspaceMember,
    Document, DocumentVersion, Tag, Comment, AuditLog
)
from .serializers import (
    UserSerializer, WorkspaceSerializer, WorkspaceMemberSerializer,
    DocumentSerializer, DocumentVersionSerializer,
    TagSerializer, CommentSerializer, AuditLogSerializer
)


# ─────────────────────────────────────────────────────
# USER VIEWSET
# Endpoints:
#   POST   /api/users/        → create user
#   GET    /api/users/{id}/   → retrieve user by UUID
# ─────────────────────────────────────────────────────

class UserViewSet(viewsets.ModelViewSet):
    """
    ModelViewSet gives us create/retrieve/update/destroy for free.
    We only expose create + retrieve per the spec (2 endpoints).
    http_method_names restricts which HTTP verbs are accepted at all.
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    http_method_names = ['get', 'post']

    def get_queryset(self):
        return User.objects.all()


# ─────────────────────────────────────────────────────
# WORKSPACE VIEWSET
# Endpoints:
#   POST   /api/workspaces/                    → create workspace
#   GET    /api/workspaces/{id}/               → retrieve with member count
#   POST   /api/workspaces/{id}/members/       → add member
#   GET    /api/workspaces/{id}/members/       → list members
#   GET    /api/workspaces/{id}/summary/       → doc/member/comment counts
# ─────────────────────────────────────────────────────

class WorkspaceViewSet(viewsets.ModelViewSet):
    serializer_class = WorkspaceSerializer
    http_method_names = ['get', 'post']
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        """
        annotate() adds 'member_count' to every Workspace object
        in the queryset. The serializer reads it via:
            member_count = serializers.IntegerField(read_only=True)
        
        Without annotate(), member_count would always be None.
        This is one of the 3 required aggregate/annotate uses.
        """
        return Workspace.objects.annotate(
            member_count=Count('members', distinct=True)
        ).select_related('owner')

    def create(self, request, *args, **kwargs):
        """
        transaction.atomic() wraps both the workspace creation
        AND the owner-as-admin member add.
        
        Why atomic? If workspace saves but the WorkspaceMember
        insert fails (e.g. DB connection drops), you'd have a
        workspace with NO admin — permanently unmanageable.
        atomic() guarantees both succeed or both roll back.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        owner = None
        if request.user.is_authenticated:
            owner = request.user
        elif 'owner' in serializer.validated_data:
            owner = serializer.validated_data['owner']
        else:
            raise serializers.ValidationError({
                'owner': 'This field is required for unauthenticated workspace creation.'
            })

        with transaction.atomic():
            workspace = serializer.save(owner=owner)
            WorkspaceMember.objects.create(
                workspace=workspace,
                user=owner,
                role=WorkspaceMember.Role.ADMIN,
            )

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post', 'get'], url_path='members')
    def members(self, request, pk=None):
        """
        POST /api/workspaces/{id}/members/ → add a member with role
        GET  /api/workspaces/{id}/members/ → list all members

        select_related('user', 'workspace') on the queryset means
        when the serializer accesses obj.user.email, no extra
        DB query fires — it was already fetched in the JOIN.
        Without this, listing 20 members = 20 extra queries (N+1).
        """
        workspace = self.get_object()

        if request.method == 'GET':
            members = WorkspaceMember.objects.filter(
                workspace=workspace
            ).select_related('user', 'workspace')
            serializer = WorkspaceMemberSerializer(members, many=True)
            return Response(serializer.data)

        # POST — add a new member
        payload = request.data.copy()
        payload['workspace'] = str(workspace.id)

        serializer = WorkspaceMemberSerializer(data=payload)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                member = serializer.save(workspace=workspace)
            return Response(
                WorkspaceMemberSerializer(member).data,
                status=status.HTTP_201_CREATED
            )
        except IntegrityError:
            # UniqueConstraint on (workspace, user) fired at DB level.
            # We catch IntegrityError and return 409 Conflict —
            # NOT 500 Internal Server Error, which would be misleading.
            return Response(
                {"detail": "This user is already a member of this workspace."},
                status=status.HTTP_409_CONFLICT
            )

    @action(detail=True, methods=['get'], url_path='summary')
    def summary(self, request, pk=None):
        """
        GET /api/workspaces/{id}/summary/
        Returns: doc count, member count, total comments

        aggregate() vs annotate():
        - annotate() adds a column to EACH ROW in a queryset
        - aggregate() collapses the ENTIRE queryset into one dict

        Here we want one dict of counts for a single workspace,
        so aggregate() is correct.
        This is one of the 3 required aggregate/annotate uses.
        """
        workspace = self.get_object()

        doc_count = Document.objects.filter(workspace=workspace).count()

        member_count = WorkspaceMember.objects.filter(
            workspace=workspace
        ).aggregate(total=Count('id'))['total']

        comment_count = Comment.objects.filter(
            document__workspace=workspace
        ).aggregate(total=Count('id'))['total']

        return Response({
            "workspace_id": str(workspace.id),
            "workspace_name": workspace.name,
            "document_count": doc_count,
            "member_count": member_count,
            "total_comments": comment_count,
        })


# ─────────────────────────────────────────────────────
# DOCUMENT VIEWSET
# Endpoints:
#   POST   /api/documents/              → create doc + first version (atomic)
#   PUT    /api/documents/{id}/         → update doc + new version (atomic)
#   GET    /api/documents/              → list with filters
#   GET    /api/documents/{id}/versions/→ all versions in order
#   GET    /api/documents/{id}/stats/   → version/comment/contributor counts
#   POST   /api/documents/{id}/tags/    → add tags to document
# ─────────────────────────────────────────────────────

class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    http_method_names = ['get', 'post', 'put']

    def get_queryset(self):
        """
        Base queryset with:
        - select_related: avoids N+1 on created_by and workspace fields
        - annotate version_count: serializer exposes this directly
        - filter logic: applied per request params below
        """
        queryset = Document.objects.select_related(
            'created_by', 'workspace'
        ).annotate(
            version_count=Count('versions', distinct=True)
        )

        # Q objects for OR filtering — plain filter() only does AND.
        # Spec requires OR logic on the list endpoint.
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(content__icontains=search)
            )

        # AND filters — each narrows the queryset further
        workspace_id = self.request.query_params.get('workspace')
        if workspace_id:
            queryset = queryset.filter(workspace__id=workspace_id)

        doc_status = self.request.query_params.get('status')
        if doc_status:
            queryset = queryset.filter(status=doc_status)

        tag_name = self.request.query_params.get('tag')
        if tag_name:
            # __ lookup traverses the M2M relationship
            queryset = queryset.filter(tags__name__icontains=tag_name)

        return queryset

    def create(self, request, *args, **kwargs):
        """
        Transaction is handled inside DocumentSerializer.create()
        which wraps Document + DocumentVersion creation atomically.
        We pass request via serializer context so the serializer
        can set created_by = request.user.
        """
        serializer = self.get_serializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        document = serializer.save()
        return Response(
            self.get_serializer(document).data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        """
        Transaction handled inside DocumentSerializer.update().
        Every PUT creates a new DocumentVersion snapshot.
        """
        instance = self.get_object()
        serializer = self.get_serializer(
            instance,
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        document = serializer.save()
        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=['get'], url_path='versions')
    def versions(self, request, pk=None):
        """
        GET /api/documents/{id}/versions/
        Returns all versions of a document ordered by version_number.
        select_related('saved_by') prevents N+1 when serializer
        accesses saved_by.email for each version row.
        """
        document = self.get_object()
        versions = DocumentVersion.objects.filter(
            document=document
        ).select_related('saved_by').order_by('version_number')

        serializer = DocumentVersionSerializer(versions, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='stats')
    def stats(self, request, pk=None):
        """
        GET /api/documents/{id}/stats/
        version_count, comment_count, contributor_count

        contributor_count = distinct users who saved a version.
        values_list() + distinct() gives us unique saved_by IDs
        without pulling full User objects — exactly when
        values_list() should be used (we only need IDs to count).
        This is one of the 3 required aggregate/annotate uses.
        """
        document = self.get_object()

        version_count = document.versions.count()

        comment_count = Comment.objects.filter(
            document=document
        ).aggregate(total=Count('id'))['total']

        # values_list returns flat list of saved_by_id values
        # distinct() deduplicates — same user saving multiple
        # versions only counts once
        contributor_ids = DocumentVersion.objects.filter(
            document=document
        ).values_list('saved_by_id', flat=True).distinct()

        contributor_count = len(contributor_ids)

        return Response({
            "document_id": str(document.id),
            "title": document.title,
            "version_count": version_count,
            "comment_count": comment_count,
            "contributor_count": contributor_count,
        })

    @action(detail=True, methods=['post'], url_path='tags')
    def add_tags(self, request, pk=None):
        """
        POST /api/documents/{id}/tags/
        Body: {"tags": ["python", "backend"]}

        get_or_create() finds existing tag by name or creates new one.
        tag.documents.add(document) uses the M2M declared on Tag.
        We return the full updated tag list after adding.
        """
        document = self.get_object()
        tag_names = request.data.get('tags', [])

        if not isinstance(tag_names, list):
            return Response(
                {"detail": "tags must be a list of strings."},
                status=status.HTTP_400_BAD_REQUEST
            )

        added_tags = []
        for name in tag_names:
            if not name.strip():
                continue
            tag, _ = Tag.objects.get_or_create(
                name=name.lower().strip()
            )
            tag.documents.add(document)
            added_tags.append(tag)

        return Response(
            TagSerializer(added_tags, many=True).data,
            status=status.HTTP_200_OK
        )


# ─────────────────────────────────────────────────────
# COMMENT VIEWSET
# Endpoints:
#   POST   /api/comments/                      → add comment or reply
#   GET    /api/comments/?document={id}        → list comments for a doc
# ─────────────────────────────────────────────────────

class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer
    http_method_names = ['get', 'post']

    def get_queryset(self):
        """
        filter() with query params — document= narrows to one doc.
        select_related('author', 'document') prevents N+1 when
        serializer accesses author.email for each comment.
        
        Only return top-level comments (parent=None) on list —
        replies are accessible via parent comment's replies relation.
        """
        queryset = Comment.objects.select_related(
            'author', 'document', 'parent'
        )

        document_id = self.request.query_params.get('document')
        if document_id:
            queryset = queryset.filter(document__id=document_id)

        # Only top-level comments in list — replies nested under parent
        queryset = queryset.filter(parent=None)

        return queryset

    def create(self, request, *args, **kwargs):
        """
        author is set from request.user in CommentSerializer.create().
        We pass request in context so the serializer can access it.
        """
        serializer = self.get_serializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        comment = serializer.save()
        return Response(
            self.get_serializer(comment).data,
            status=status.HTTP_201_CREATED
        )


# ─────────────────────────────────────────────────────
# TAG VIEWSET
# Endpoints:
#   POST   /api/tags/   → create a tag
# ─────────────────────────────────────────────────────

class TagViewSet(viewsets.ModelViewSet):
    """
    ModelSerializer validates unique constraint on Tag.name
    before hitting the DB, returning a clean 400 on duplicates.
    """
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    http_method_names = ['post', 'get']


# ─────────────────────────────────────────────────────
# AUDIT LOG VIEWSET
# Endpoints:
#   GET    /api/audit-logs/   → filter by actor and date range
# ─────────────────────────────────────────────────────

class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ReadOnlyModelViewSet = only GET list and GET retrieve.
    AuditLogs are never created or modified via API.

    Filtering uses __gte and __lte lookups for date range —
    exactly the lookup types the spec requires for this endpoint.
    """
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        queryset = AuditLog.objects.select_related('actor')

        # Filter by actor UUID
        actor_id = self.request.query_params.get('actor')
        if actor_id:
            queryset = queryset.filter(actor__id=actor_id)

        # Date range filters using __gte and __lte lookups
        date_from = self.request.query_params.get('from')
        if date_from:
            queryset = queryset.filter(timestamp__gte=date_from)

        date_to = self.request.query_params.get('to')
        if date_to:
            queryset = queryset.filter(timestamp__lte=date_to)

        # Filter by action type
        action_type = self.request.query_params.get('action')
        if action_type:
            queryset = queryset.filter(action=action_type)

        return queryset
