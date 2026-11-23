"""Core infrastructure modules.

Keep this package initializer free of application-model imports. Models import
``app.core.database.Base``, so importing models here would create a package
initialization cycle before the model classes are defined.
"""
