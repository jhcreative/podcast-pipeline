# jobs/ — job handler modules
# Each module exposes a single run(job_id, episode_id, payload) function.
# Handlers raise on failure; the worker retries with exponential back-off.
