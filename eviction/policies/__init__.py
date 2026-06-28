"""Concrete eviction policies, one per module.

Adding a policy = adding a module here and registering it in the parent
package's factory. No existing file needs to change (Open/Closed).
"""
from eviction.policies.noeviction import NoEvictionPolicy
from eviction.policies.random_policy import RandomPolicy

__all__ = ["NoEvictionPolicy", "RandomPolicy"]
