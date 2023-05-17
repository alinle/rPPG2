import random

import numpy as np
import torch
import wandb
import datetime
from rppg.loss import loss_fn
from rppg.models import get_model
from rppg.optim import optimizer
from rppg.config import get_config
from rppg.dataset_loader import (dataset_loader, dataset_split, data_loader)
from rppg.preprocessing.dataset_preprocess import preprocessing
# from rppg.train import train_fn, val_fn, test_fn
from rppg.MAML import MAML
from rppg.run import test_fn
from tqdm import tqdm
import os

SEED = 0

# for Reproducible model
torch.manual_seed(SEED)
torch.cuda.manual_seed(SEED)
torch.cuda.manual_seed_all(SEED)  # if use multi-GPU
torch.backends.cudnn.deterministic = False
torch.backends.cudnn.benchmark = False
np.random.seed(SEED)
random.seed(SEED)

generator = torch.Generator()
generator.manual_seed(SEED)

if __name__ == "__main__":

    cfg = get_config("../../rppg/configs/FIT_METAPHYS_MMPD.yaml")
    if cfg.preprocess.flag:
        preprocessing(
            dataset_root_path=cfg.data_root_path,
            preprocess_cfg=cfg.preprocess
        )

    # load dataset
    if cfg.fit.meta.flag:
        dataset = dataset_loader(
            save_root_path=cfg.dataset_path,
            model_name=cfg.fit.model,
            dataset_name=[cfg.fit.train.dataset, cfg.fit.test.dataset],
            time_length=cfg.fit.time_length,
            overlap_interval=cfg.fit.overlap_interval,
            img_size=cfg.fit.img_size,
            train_flag=cfg.fit.train_flag,
            eval_flag=cfg.fit.eval_flag,
            meta=cfg.fit.meta.flag
        )

        tasks = data_loader(datasets=dataset,
                            batch_size=cfg.fit.train.batch_size,
                            meta=cfg.fit.meta.flag)

    model = get_model(
        model_name=cfg.fit.model,
        time_length=cfg.fit.time_length)
    if cfg.fit.meta.pretrain.flag:
        pretrained_path = cfg.model_path + cfg.fit.model + "_" + cfg.fit.meta.pretrain.dataset + ".pt"
        if os.path.isfile(pretrained_path):
            model.load_state_dict(torch.load(pretrained_path))
            print("Load pre-trained model")
        else:
            print("No pre-trained model exist, start training")
            exec(open("configs/FIT_" + str(cfg.fit.model).upper() + "_UBFC_UBFC.yaml").read())
            print("Pre-training complete, start training meta-learning model")
            pretrained_path = cfg.model_path + cfg.fit.model + "_" + cfg.fit.meta.pretrain.dataset + ".pt"
            model.load_state_dict(torch.load(pretrained_path))
    else:
        print("Cold start with no pre-trained model")
    wandb_cfg = get_config("../../rppg/configs/WANDB_CONFG.yaml")

    if wandb_cfg.flag:
        wandb.init(project=wandb_cfg.wandb_project_name,
                   entity=wandb_cfg.wandb_entity,
                   name="Meta_"+cfg.fit.model + "/TRAIN_DATA:" +
                        cfg.fit.train.dataset + "/TEST_DATA:" +
                        cfg.fit.test.dataset + "/" +
                        str(cfg.fit.time_length) + "/" +
                        datetime.datetime.now().strftime('%m-%d%H:%M:%S'))
        wandb.config = {
            "learning_rate": cfg.fit.train.learning_rate,
            "epochs": cfg.fit.train.epochs,
            "batch_size": cfg.fit.batch_size
        }

    if cfg.fit.meta.flag:
        meta = MAML(model=model,
                    inner_optim=cfg.fit.meta.inner_optim,
                    outer_optim=cfg.fit.meta.outer_optim,
                    inner_loss=cfg.fit.meta.inner_loss,
                    outer_loss=cfg.fit.meta.outer_loss,
                    inner_lr=cfg.fit.meta.inner_lr,
                    outer_lr=cfg.fit.meta.outer_lr,
                    num_updates=5)

        # cost_per_epoch = []
        for epoch in range(cfg.fit.train.epochs):
            meta.meta_update(tasks[:20], epoch)
            cost_per_task = []
            if cfg.fit.meta.fine_tune.flag:
                for support, query in tasks[20:]:
                    individual_model = meta.inner_update(support)
                    cost_per_task.append(test_fn(epoch=epoch,
                                                 model=individual_model,
                                                 dataloaders=query,
                                                 model_name=cfg.fit.model,
                                                 cal_type=cfg.fit.test.cal_type,
                                                 metrics=cfg.fit.test.metric,
                                                 wandb_flag=False))

                mean_cost = np.nan_to_num(np.array(cost_per_task)).mean(axis=0)
                mae_cost_mean, rmse_cost_mean, mape_cost_mean, pearson_cost_mean = mean_cost[0], mean_cost[1], \
                                                                                   mean_cost[2], mean_cost[3]
                print("Epoch: {} | MAE: {:.4f} | RMSE: {:.4f} | MAPE: {:.4f} | Pearson: {:.4f}".format(epoch,
                                                                                                       mae_cost_mean,
                                                                                                       rmse_cost_mean,
                                                                                                       mape_cost_mean,
                                                                                                       pearson_cost_mean))
                wandb.log({"MAE": mae_cost_mean,
                           "RMSE": rmse_cost_mean,
                           "MAPE": mape_cost_mean,
                           "Pearson": pearson_cost_mean}, step=epoch)

    print("END")
