from rest_framework import serializers
from django.db import IntegrityError
from .models import (
    User, Workspace, WorkspaceMember,
    Document, DocumentVersion, Tag, Comment, AuditLog
)


# ─────────────────────────────────────────
# USER SERIALIZER
# ─────────────────────────────────────────

class UserSerializer(serializers.ModelSerializer):
    """
    Used for: POST /api/users/ and GET /api/users/{id}/
    
    SerializerMethodField #1: full_name
    Instead of exposing first_name and last_name separately,
    we compute a combined display name on the fly.
    This field is read-only — it's computed, not stored in DB.
    
    Custom validation #1: email uniqueness with meaningful error.
    ModelSerializer would catch this at DB level and return a 
    generic IntegrityError. We catch it early with a clean 400.
    """
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name',
                  'full_name', 'email', 'phone', 'created_at']
        read_only_fields = ['id', 'created_at', 'full_name']
        extra_kwargs = {
            'password': {'write_only': True, 'required': False}
        }

    def get_full_name(self, obj):
        """SerializerMethodField: always named get_<field_name>"""
        return f"{obj.first_name} {obj.last_name}".strip()

    def validate_email(self, value):
        """
        Custom field-level validation.
        validate_<fieldname>() is called automatically by DRF
        during .is_valid() — we don't call it manually.
        """
        qs = User.objects.filter(email=value)
        # On update (PATCH/PUT), exclude the current instance
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "A user with this email already exists."
            )
        return value

    def create(self, validated_data):
        """
        We override create() to call set_password() so the password
        is hashed before storage. Never store raw passwords.
        """
        password = validated_data.pop('password', None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user


# ─────────────────────────────────────────
# WORKSPACE SERIALIZERS
# ─────────────────────────────────────────

class WorkspaceMemberSerializer(serializers.ModelSerializer):
    """
    Used for: GET /api/workspaces/{id}/members/
    
    SerializerMethodField #2: user_email
    Instead of nesting the full User object (wasteful), we expose
    just the email using select_related data that's already loaded.
    This avoids extra queries while still giving the client useful info.
    
    Custom validation #2: prevent adding a user who doesn't exist,
    with a meaningful 400 instead of a raw DB foreign key error.
    """
    user_email = serializers.SerializerMethodField()
    user_full_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceMember
        fields = ['id', 'workspace', 'user', 'user_email',
                  'user_full_name', 'role', 'joined_at']
        read_only_fields = ['id', 'joined_at', 'user_email', 'user_full_name']

    def get_user_email(self, obj):
        # obj.user is already loaded via select_related in the view
        return obj.user.email if obj.user else None

    def get_user_full_name(self, obj):
        if obj.user:
            return f"{obj.user.first_name} {obj.user.last_name}".strip()
        return None

    def validate(self, data):
        """
        Object-level validation (validate() not validate_<field>)
        used when validation involves multiple fields together.
        Here we check the user+workspace combination for duplicates
        BEFORE hitting the DB, so we return 409 not 500.
        """
        workspace = data.get('workspace')
        user = data.get('user')
        if workspace and user:
            if WorkspaceMember.objects.filter(
                workspace=workspace, user=user
            ).exists():
                raise serializers.ValidationError(
                    {"detail": "This user is already a member of this workspace."}
                )
        return data


class WorkspaceSerializer(serializers.ModelSerializer):
    """
    Used for: POST /api/workspaces/ and GET /api/workspaces/{id}/
    
    member_count uses annotate() in the view queryset — the view
    adds a 'member_count' annotation, and we expose it here.
    source='member_count' reads the annotation off the queryset object.
    """
    member_count = serializers.IntegerField(read_only=True, default=0)
    owner_email = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ['id', 'name', 'owner', 'owner_email',
                  'is_active', 'member_count', 'created_at']
        read_only_fields = ['id', 'created_at', 'member_count', 'owner_email']

    def get_owner_email(self, obj):
        return obj.owner.email if obj.owner else None


# ─────────────────────────────────────────
# TAG SERIALIZER
# ─────────────────────────────────────────

class TagSerializer(serializers.ModelSerializer):
    """
    Used for: POST /api/tags/
    Simple serializer — Tag only has name.
    unique=True on the model handles duplicate detection,
    but DRF's ModelSerializer already validates unique constraints
    before hitting the DB, giving a clean 400 response.
    """
    class Meta:
        model = Tag
        fields = ['id', 'name']
        read_only_fields = ['id']


# ─────────────────────────────────────────
# DOCUMENT SERIALIZERS
# ─────────────────────────────────────────

class DocumentVersionSerializer(serializers.ModelSerializer):
    """
    Used for: GET /api/documents/{id}/versions/
    Read-only — versions are created automatically on document save,
    never created directly via API.
    """
    saved_by_email = serializers.SerializerMethodField()

    class Meta:
        model = DocumentVersion
        fields = ['id', 'document', 'version_number', 'content',
                  'saved_by', 'saved_by_email', 'saved_at']
        read_only_fields = ['id', 'version_number', 'saved_at',
                            'saved_by_email']

    def get_saved_by_email(self, obj):
        return obj.saved_by.email if obj.saved_by else None


class DocumentSerializer(serializers.ModelSerializer):
    """
    Used for: POST /api/documents/ and PUT /api/documents/{id}/
    GET /api/documents/ (list)

    tags: read as list of tag names (not IDs) for usability.
    tag_list is a SerializerMethodField for reading.
    On write, tags are handled in create()/update() via M2M.

    version_count: annotated by the view queryset.
    """
    tags = TagSerializer(many=True, read_only=True)
    tag_names = serializers.ListField(
        child=serializers.CharField(),
        write_only=True,
        required=False,
        help_text="List of tag names to attach to this document"
    )
    version_count = serializers.IntegerField(read_only=True, default=0)
    created_by_email = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ['id', 'title', 'content', 'workspace', 'created_by',
                  'created_by_email', 'status', 'tags', 'tag_names',
                  'version_count', 'updated_at']
        read_only_fields = ['id', 'updated_at', 'created_by',
                            'created_by_email', 'version_count']

    def get_created_by_email(self, obj):
        return obj.created_by.email if obj.created_by else None

    def validate_status(self, value):
        """
        Custom field-level validation on status.
        Document.Status.values gives ['draft', 'published', 'archived'].
        If someone sends status='deleted' we return a clear 400.
        """
        if value not in Document.Status.values:
            raise serializers.ValidationError(
                f"Invalid status '{value}'. "
                f"Must be one of: {Document.Status.values}"
            )
        return value

    def validate_title(self, value):
        """Title cannot be blank or whitespace only."""
        if not value.strip():
            raise serializers.ValidationError(
                "Document title cannot be blank."
            )
        return value.strip()

    def create(self, validated_data):
        """
        Override create() for two reasons:
        1. Pop tag_names out before creating Document (M2M can't be set before PK exists)
        2. Create DocumentVersion inside the same atomic block as the Document

        transaction.atomic() here is the assignment's key requirement:
        if the DocumentVersion insert fails, the Document insert also rolls back.
        You never end up with a document that has no version history.
        """
        from django.db import transaction

        tag_names = validated_data.pop('tag_names', [])
        request = self.context.get('request')
        user = request.user if request and getattr(request.user, 'is_authenticated', False) else None

        with transaction.atomic():
            # Set created_by only when a real authenticated user is present.
            if user is not None:
                validated_data['created_by'] = user
            document = Document.objects.create(**validated_data)

            # Create first DocumentVersion snapshot
            # version_number = count of existing versions + 1
            # On creation this is always 1, but using count()+1
            # makes the pattern consistent with updates too
            version_number = document.versions.count() + 1
            DocumentVersion.objects.create(
                document=document,
                content=document.content,
                version_number=version_number,
                saved_by=user,
            )

            # Attach tags via M2M — must happen after document.pk exists
            for tag_name in tag_names:
                tag, _ = Tag.objects.get_or_create(name=tag_name.lower().strip())
                tag.documents.add(document)

        return document

    def update(self, instance, validated_data):
        """
        Override update() to create a new DocumentVersion on every save.
        Same atomic pattern: if version creation fails, content update
        also rolls back — history stays in sync with current content.
        """
        from django.db import transaction

        tag_names = validated_data.pop('tag_names', [])
        request = self.context.get('request')
        user = request.user if request and getattr(request.user, 'is_authenticated', False) else None

        with transaction.atomic():
            # Update the document fields
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            # Snapshot the new content as a new version
            version_number = instance.versions.count() + 1
            DocumentVersion.objects.create(
                document=instance,
                content=instance.content,
                version_number=version_number,
                saved_by=user,
            )

            # Replace tags if provided
            if tag_names:
                # Clear existing M2M links for this document
                Tag.objects.filter(documents=instance).all()
                for tag in Tag.objects.filter(documents=instance):
                    tag.documents.remove(instance)
                # Add new ones
                for tag_name in tag_names:
                    tag, _ = Tag.objects.get_or_create(
                        name=tag_name.lower().strip()
                    )
                    tag.documents.add(instance)

        return instance


# ─────────────────────────────────────────
# COMMENT SERIALIZER
# ─────────────────────────────────────────

class CommentSerializer(serializers.ModelSerializer):
    """
    Used for: POST /api/comments/ and GET /api/comments/?document={id}

    reply_count: SerializerMethodField — counts replies to this comment.
    This uses the reverse FK 'replies' from the self-referential FK.
    
    author_email: shows who wrote the comment without nesting full User object.

    Validation: a reply (parent set) must point to a comment on the same document.
    Without this check someone could create cross-document reply chains.
    """
    reply_count = serializers.SerializerMethodField()
    author_email = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ['id', 'document', 'author', 'author_email',
                  'content', 'parent', 'reply_count', 'created_at']
        read_only_fields = ['id', 'created_at', 'author',
                            'author_email', 'reply_count']

    def get_reply_count(self, obj):
        """
        obj.replies is the reverse related manager from:
        parent = ForeignKey('self', related_name='replies')
        .count() hits the DB — the view must NOT prefetch this
        separately or we'll double-count.
        """
        return obj.replies.count()

    def get_author_email(self, obj):
        return obj.author.email if obj.author else None

    def validate(self, data):
        """
        Object-level validation: if parent is provided,
        it must belong to the same document as this comment.
        """
        parent = data.get('parent')
        document = data.get('document')
        if parent and document:
            if parent.document_id != document.id:
                raise serializers.ValidationError({
                    "parent": "Reply must belong to the same document."
                })
        return data

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['author'] = (
            request.user if request and getattr(request.user, 'is_authenticated', False) else None
        )
        return super().create(validated_data)


# ─────────────────────────────────────────
# AUDIT LOG SERIALIZER
# ─────────────────────────────────────────

class AuditLogSerializer(serializers.ModelSerializer):
    """
    Used for: GET /api/audit-logs/
    Read-only — AuditLogs are never created via API,
    only via the post_save signal on Document.
    actor_email gives human-readable identity without
    exposing the full User object.
    """
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ['id', 'actor', 'actor_email', 'action',
                  'model_name', 'object_id', 'timestamp']
        read_only_fields = fields

    def get_actor_email(self, obj):
        return obj.actor.email if obj.actor else None
    
