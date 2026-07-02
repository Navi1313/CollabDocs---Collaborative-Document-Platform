import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings


class User(AbstractUser):
    """
    Custom User model replacing Django's built-in User.
    - id: UUID instead of integer (security + distributed compatibility)
    - email: CharField(254) unique=True per spec (NOT EmailField)
    - phone: unique identifier, optional
    - created_at: timestamp
    AbstractUser already provides first_name, last_name, username, password.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.CharField(max_length=254, unique=True)
    phone = models.CharField(max_length=15, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'users'

    def __str__(self):
        return self.email


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_workspaces',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'workspaces'

    def __str__(self):
        return self.name


class WorkspaceMember(models.Model):
    """
    Join table between Workspace and User, carrying the role field.
    UniqueConstraint prevents same user being added twice to same workspace.
    Without it, 'what is this user's role?' becomes ambiguous.
    """
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        EDITOR = 'editor', 'Editor'
        VIEWER = 'viewer', 'Viewer'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='members',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workspace_memberships',
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.VIEWER,
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'workspace_memberships'
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'user'],
                name='unique_workspace_member'
            )
        ]

    def __str__(self):
        return f'{self.user} - {self.workspace} ({self.role})'


class Document(models.Model):
    """
    Note: ManyToMany with Tag is declared on Tag, not here.
    Access tags via: document.tags.all() (works via related_name='tags' on Tag)
    """
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        ARCHIVED = 'archived', 'Archived'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_documents',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'documents'

    def __str__(self):
        return self.title


class Tag(models.Model):
    """
    ManyToManyField declared HERE on Tag per spec.
    related_name='tags' means you can still do document.tags.all().
    To add tag to doc: tag.documents.add(doc) OR doc.tags.add(tag)
    To filter docs by tag: Document.objects.filter(tags__name='python')
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    documents = models.ManyToManyField(
        Document,
        related_name='tags',
        blank=True,
    )

    class Meta:
        db_table = 'tags'

    def __str__(self):
        return self.name


class DocumentVersion(models.Model):
    """
    Snapshot of Document content at every save.
    version_number computed as document.versions.count() + 1
    inside transaction.atomic() in the view — never auto-incremented here.
    saved_by tracks who triggered this version — needed for contributor count.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='versions',
    )
    content = models.TextField()
    version_number = models.PositiveIntegerField()
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='saved_versions',
    )
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'document_versions'
        ordering = ['version_number']
        constraints = [
            models.UniqueConstraint(
                fields=['document', 'version_number'],
                name='unique_document_version'
            )
        ]

    def __str__(self):
        return f'{self.document.title} v{self.version_number}'


class Comment(models.Model):
    """
    Self-referential FK enables threaded replies.
    parent=None → top-level comment
    parent=<comment_id> → reply to that comment
    comment.replies.all() → get all replies to a comment
    author named exactly per spec (NOT 'user').
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='comments',
    )
    content = models.TextField()
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='replies',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'comments'

    def __str__(self):
        return f'Comment by {self.author} on {self.document}'


class AuditLog(models.Model):
    """
    Written automatically via post_save signal on Document.
    Never written manually in views — signal fires regardless of how
    a Document is saved, making the audit trail reliable.
    object_id is CharField per spec — stores UUID as plain string.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs',
    )
    action = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-timestamp']

    def __str__(self):
        return f'{self.actor} - {self.action} on {self.model_name}({self.object_id})'