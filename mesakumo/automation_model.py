"""Shared provider budgets, invocation metrics and structured validation."""
import os
import random
import re
import time

from pydantic import ValidationError

from .automation_store import canonical
from .gemini import ProviderError, SYSTEM
from .models import ModelUnavailable, RequestyModel


def model_profile(model):
    return [type(model).__name__, getattr(model, 'model', None), os.environ.get('MESAKUMO_GEMINI_MAX_OUTPUT')]


class ModelCalls:
    def __init__(self, store, model, project, wait, *, sleep=time.sleep):
        self.store = store
        self.model = model
        if isinstance(model, RequestyModel):
            model.client = model.client.with_options(max_retries=0)
        self.project = project
        self.wait = wait
        self.sleep = sleep
        model_id = getattr(model, 'model', None) or type(model).__name__
        self.name = model_id
        scope = os.environ.get('MESAKUMO_QUOTA_PROJECT') or type(model).__name__
        self.bucket = scope + '/' + model_id
        self.rpm = int(os.environ.get('MESAKUMO_RPM') or (4 if 'hydrogen' in model_id else 12))
        self.tpm = int(os.environ.get('MESAKUMO_INPUT_TPM') or 200_000)
        self.rpd = int(os.environ.get('MESAKUMO_RPD') or 0)
        if min(self.rpm, self.tpm) <= 0 or self.rpd < 0:
            raise ValueError('Invalid model quota settings.')

    def reserve(self, tokens):
        if tokens > self.tpm:
            raise ProviderError('One prompt exceeds the configured input token budget.', truncated=True)
        while True:
            now = time.time()
            with self.store.write() as db:
                daily = db.execute('SELECT count(*),min(called) FROM model_calls WHERE quota_bucket=? AND called>?',
                                   (self.bucket, now - 86400)).fetchone()
                if self.rpd and daily[0] >= self.rpd:
                    raise ProviderError('The configured model daily quota is exhausted. Resume after it resets.')
                minute = db.execute("""
                    SELECT count(*),coalesce(sum(coalesce(input,estimated)),0),min(called)
                    FROM model_calls WHERE quota_bucket=? AND called>?
                """, (self.bucket, now - 60)).fetchone()
                if minute[0] < self.rpm and minute[1] + tokens <= self.tpm:
                    return db.execute("""
                        INSERT INTO model_calls(project,model,called,quota_bucket,estimated)
                        VALUES (?,?,?,?,?)
                    """, (self.project, self.name, now, self.bucket, tokens)).lastrowid
            self.wait('Waiting for model quota')
            self.sleep(min(15, max(.1, minute[2] + 60 - now)))

    def __call__(self, prompt, shape, *, max_tokens=4000):
        schema = shape.model_json_schema()
        if not hasattr(self.model, 'complete_task'):
            raise ModelUnavailable('Configure a structured-output model for preparation.')
        for attempt in range(3):
            # Allow for the prompt, system instruction, and response schema before provider usage arrives.
            estimate = (len((prompt + SYSTEM + canonical(schema)).encode()) + 1) // 2
            call_id = self.reserve(estimate)
            try:
                raw = self.model.complete_task(prompt, system=SYSTEM, schema=schema,
                                               schema_name=shape.__name__, max_tokens=max_tokens)
                return shape.model_validate_json(raw).model_dump()
            except ValidationError:
                error = ProviderError('The provider response did not match the required schema.', transient=True, invalid_content=True)
            except ProviderError as exc:
                error = exc
            except ModelUnavailable as exc:
                status = re.search(r'HTTP (\d+)', str(exc))
                code = int(status[1]) if status else 0
                error = ProviderError(str(exc), transient=code in (408, 429, 500, 502, 503, 504)
                                      or 'timed out' in str(exc), truncated='finish normally' in str(exc))
            finally:
                usage = getattr(self.model, 'usage', {})
                with self.store.connect() as db:
                    db.execute('UPDATE model_calls SET input=?,output=?,thinking=? WHERE id=?',
                               (usage.get('input'), usage.get('output'), usage.get('thinking'), call_id))
            if not error.transient or attempt == 2:
                raise error from None
            self.wait('Retrying transient model failure')
            self.sleep(min(12, 2 ** attempt + random.random()))


def recoverable_content(error):
    """A bad payload can be isolated. Access and quota failures apply to the job."""
    return isinstance(error, ValueError) or (
        isinstance(error, ProviderError) and (error.truncated or error.invalid_content)
    )
