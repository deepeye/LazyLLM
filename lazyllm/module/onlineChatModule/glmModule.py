import json
import os
import uuid
import requests
from typing import Tuple, List
from urllib.parse import urljoin
import lazyllm
from .onlineChatModuleBase import OnlineChatModuleBase
from .fileHandler import FileHandlerBase

class GLMModule(OnlineChatModuleBase, FileHandlerBase):
    TRAINABLE_MODEL_LIST = ["chatglm3-6b", "chatglm_12b", "chatglm_32b", "chatglm_66b", "chatglm_130b"]
    MODEL_NAME = "glm-4"

    def __init__(self, base_url: str = "https://open.bigmodel.cn/api/paas/v4/", model: str = None,
                 api_key: str = None, stream: str = True, return_trace: bool = False, **kwargs):
        OnlineChatModuleBase.__init__(self, model_series="GLM", api_key=api_key or lazyllm.config['glm_api_key'],
                                      model_name=model or lazyllm.config['glm_model_name'] or GLMModule.MODEL_NAME,
                                      base_url=base_url, stream=stream, return_trace=return_trace, **kwargs)
        FileHandlerBase.__init__(self)
        self.default_train_data = {
            "model": None,
            "training_file": None,
            "validation_file": None,
            "extra_hyperparameters": {
                "fine_tuning_method": None,  # lora\full, default: lora,
                "fine_tuning_parameters": {
                    "max_sequence_length": None  # [1, 8192](int), default: 8192
                }
            },
            "hyperparameters": {
                "learning_rate_multiplier": 0.01,  # (0,5] , default: 1.0
                "batch_size": None,  # [1, 32], default: 8
                "n_epochs": 1,  # [1, 10], default: 3
            },
            "suffix": None,
            "request_id": None
        }
        self.fine_tuning_job_id = None

    def _get_system_prompt(self):
        return ("You are ChatGLM, an AI assistant developed based on a language model trained by Zhipu AI. "
                "Your task is to provide appropriate responses and support for users' questions and requests.")

    def _get_models_list(self):
        return ["glm-4", "glm-4v", "glm-3-turbo", "chatglm-turbo", "cogview-3", "embedding-2", "text-embedding"]

    def _convert_file_format(self, filepath: str) -> str:
        with open(filepath, 'r', encoding='utf-8') as fr:
            dataset = [json.loads(line) for line in fr]

        json_strs = []
        for ex in dataset:
            lineEx = {"messages": []}
            messages = ex.get("messages", [])
            for message in messages:
                role = message.get("role", "")
                content = message.get("content", "")
                if role in ["system", "user", "assistant"]:
                    lineEx["messages"].append({"role": role, "content": content})
            json_strs.append(json.dumps(lineEx, ensure_ascii=False))

        return "\n".join(json_strs)

    def _upload_train_file(self, train_file):
        headers = {
            "Authorization": "Bearer " + self._api_key
        }

        url = urljoin(self._base_url, "files")
        self.get_finetune_data(train_file)

        file_object = {
            "purpose": (None, "fine-tune", None),
            "file": (os.path.basename(train_file), self._dataHandler, "application/json")
        }

        with requests.post(url, headers=headers, files=file_object) as r:
            if r.status_code != 200:
                raise requests.RequestException('\n'.join([c.decode('utf-8') for c in r.iter_content(None)]))

            # delete temporary training file
            self._dataHandler.close()
            return r.json()["id"]

    def _update_kw(self, data, normal_config):
        cur_data = self.default_train_data.copy()
        cur_data.update(data)

        cur_data["extra_hyperparameters"]["fine_tuning_method"] = normal_config["finetuning_type"].strip().lower()
        cur_data["extra_hyperparameters"]["fine_tuning_parameters"]["max_sequence_length"] = normal_config["cutoff_len"]
        cur_data["hyperparameters"]["learning_rate_multiplier"] = normal_config["learning_rate"]
        cur_data["hyperparameters"]["batch_size"] = normal_config["batch_size"]
        cur_data["hyperparameters"]["n_epochs"] = normal_config["num_epochs"]
        cur_data["suffix"] = str(uuid.uuid4())[:7]
        return cur_data

    def _create_finetuning_job(self, train_model, train_file_id, **kw) -> Tuple[str, str]:
        url = urljoin(self._base_url, "fine_tuning/jobs")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        data = {
            "model": train_model,
            "training_file": train_file_id
        }
        if len(kw) > 0:
            if 'finetuning_type' in kw:
                data = self._update_kw(data, kw)
            else:
                data.update(kw)

        with requests.post(url, headers=headers, json=data) as r:
            if r.status_code != 200:
                raise requests.RequestException('\n'.join([c.decode('utf-8') for c in r.iter_content(None)]))

            fine_tuning_job_id = r.json()["id"]
            self.fine_tuning_job_id = fine_tuning_job_id
            status = self._status_mapping(r.json()["status"])
            return (fine_tuning_job_id, status)

    def _cancel_finetuning_job(self, fine_tuning_job_id=None):
        if not fine_tuning_job_id and not self.fine_tuning_job_id:
            return 'Invalid'
        job_id = fine_tuning_job_id if fine_tuning_job_id else self.fine_tuning_job_id
        fine_tune_url = os.path.join(self._base_url, f"fine_tuning/jobs/{job_id}/cancel")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        with requests.post(fine_tune_url, headers=headers) as r:
            if r.status_code != 200:
                raise requests.RequestException('\n'.join([c.decode('utf-8') for c in r.iter_content(None)]))
        status = r.json()['status']
        if status == 'cancelled':
            return 'Cancelled'
        else:
            return f'JOB {job_id} status: {status}'

    def _query_finetuned_jobs(self):
        fine_tune_url = os.path.join(self._base_url, "fine_tuning/jobs/")
        headers = {
            "Authorization": f"Bearer {self._api_key}"
        }
        with requests.get(fine_tune_url, headers=headers) as r:
            if r.status_code != 200:
                raise requests.RequestException('\n'.join([c.decode('utf-8') for c in r.iter_content(None)]))
        return r.json()

    def _get_finetuned_model_names(self) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        model_data = self._query_finetuned_jobs()
        res = list()
        for model in model_data['data']:
            res.append([model['id'], model['fine_tuned_model'], self._status_mapping(model['status'])])
        return res

    def _status_mapping(self, status):
        if status == 'succeeded':
            return 'Done'
        elif status == 'failed':
            return 'Failed'
        elif status == 'cancelled':
            return 'Cancelled'
        elif status == 'running':
            return 'Running'
        else:  # create, validating_files, queued
            return 'Pending'

    def _query_job_status(self, fine_tuning_job_id=None):
        if not fine_tuning_job_id and not self.fine_tuning_job_id:
            raise RuntimeError("No job ID specified. Please ensure that a valid 'fine_tuning_job_id' is "
                               "provided as an argument or started a training job.")
        job_id = fine_tuning_job_id if fine_tuning_job_id else self.fine_tuning_job_id
        _, status = self._query_finetuning_job(job_id)
        return self._status_mapping(status)

    def _get_log(self, fine_tuning_job_id=None):
        if not fine_tuning_job_id and not self.fine_tuning_job_id:
            raise RuntimeError("No job ID specified. Please ensure that a valid 'fine_tuning_job_id' is "
                               "provided as an argument or started a training job.")
        job_id = fine_tuning_job_id if fine_tuning_job_id else self.fine_tuning_job_id
        fine_tune_url = os.path.join(self._base_url, f"fine_tuning/jobs/{job_id}/events")
        headers = {
            "Authorization": f"Bearer {self._api_key}"
        }
        with requests.get(fine_tune_url, headers=headers) as r:
            if r.status_code != 200:
                raise requests.RequestException('\n'.join([c.decode('utf-8') for c in r.iter_content(None)]))
        return job_id, r.json()

    def _get_curr_job_model_id(self):
        if not self.fine_tuning_job_id:
            return None, None
        model_id, _ = self._query_finetuning_job(self.fine_tuning_job_id)
        return self.fine_tuning_job_id, model_id

    def _query_finetuning_job_info(self, fine_tuning_job_id):
        fine_tune_url = os.path.join(self._base_url, f"fine_tuning/jobs/{fine_tuning_job_id}")
        headers = {
            "Authorization": f"Bearer {self._api_key}"
        }
        with requests.get(fine_tune_url, headers=headers) as r:
            if r.status_code != 200:
                raise requests.RequestException('\n'.join([c.decode('utf-8') for c in r.iter_content(None)]))
        return r.json()

    def _query_finetuning_job(self, fine_tuning_job_id) -> Tuple[str, str]:
        info = self._query_finetuning_job_info(fine_tuning_job_id)
        status = info['status']
        fine_tuned_model = info["fine_tuned_model"] if 'fine_tuned_model' in info else None
        return (fine_tuned_model, status)

    def _query_finetuning_cost(self, fine_tuning_job_id):
        info = self._query_finetuning_job_info(fine_tuning_job_id)
        if 'trained_tokens' in info and info['trained_tokens']:
            return info['trained_tokens']
        else:
            return None

    def _create_deployment(self) -> Tuple[str]:
        return (self._model_name, "RUNNING")

    def _query_deployment(self, deployment_id) -> str:
        return "RUNNING"
