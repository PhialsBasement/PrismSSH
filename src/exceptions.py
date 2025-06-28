"""Custom exceptions for PrismSSH."""


class PrismSSHError(Exception):
    """Base exception for PrismSSH."""
    pass


class SSHConnectionError(PrismSSHError):
    """Raised when SSH connection fails."""
    pass


class SSHAuthenticationError(PrismSSHError):
    """Raised when SSH authentication fails."""
    pass


class EncryptionError(PrismSSHError):
    """Raised when password encryption/decryption fails."""
    pass


class SessionError(PrismSSHError):
    """Raised when session operations fail."""
    pass


class ConfigurationError(PrismSSHError):
    """Raised when configuration is invalid."""
    pass


class SFTPError(PrismSSHError):
    """Raised when SFTP operations fail."""
    pass