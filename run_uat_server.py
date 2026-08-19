import os
os.environ["ENABLE_DEMO_ADMIN_ACTIONS"] = "true"
os.environ["COZE_ALLOW_LOCAL_WHEN_UNCONFIGURED"] = "true"
os.environ["SEED_DEMO_DATA"] = "true"

import uvicorn
uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
