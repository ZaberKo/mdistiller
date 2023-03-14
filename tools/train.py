from mdistiller.engine import trainer_dict
from mdistiller.engine.cfg import show_cfg, dump_cfg
from mdistiller.engine.cfg import CFG as cfg
from mdistiller.engine.utils import load_checkpoint, log_msg
from mdistiller.dataset import get_dataset
from mdistiller.distillers import distiller_dict
from mdistiller.models import (
    cifar_model_dict, 
    cifar_aug_model_dict,
    imagenet_model_dict
)

import os
import argparse
import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
from datetime import datetime
import numpy as np
import random

cudnn.benchmark = True


def main(cfg, resume, opts, group_flag=False, id=""):
    experiment_name = cfg.EXPERIMENT.NAME
    if experiment_name == "":
        experiment_name = cfg.EXPERIMENT.TAG
    tags = cfg.EXPERIMENT.TAG.split(",")
    if opts:
        addtional_tags = ["{}:{}".format(k, v)
                          for k, v in zip(opts[::2], opts[1::2])]
        tags += addtional_tags
        experiment_name += "|"+",".join(addtional_tags)

    # experiment_name = f"{cfg.EXPERIMENT.PROJECT}/{experiment_name}"
    experiment_name = cfg.EXPERIMENT.PROJECT + "/" + experiment_name
    if cfg.LOG.WANDB:
        try:
            import wandb

            wandb.init(
                project=cfg.EXPERIMENT.PROJECT,
                name=experiment_name,
                tags=tags,
                config=dump_cfg(cfg),
                group=experiment_name+"_group" if group_flag else None,
                # helps resolve "InitStartError: Error communicating with wandb process"
                settings=wandb.Settings(start_method="fork")
            )
        except:
            print(log_msg("Failed to use WANDB", "INFO"))
            cfg.defrost()
            cfg.LOG.WANDB = False
            cfg.freeze()

    # cfg & loggers
    show_cfg(cfg)
    # init dataloader & models
    train_loader, val_loader, num_data, num_classes = get_dataset(cfg)

    # vanilla
    if cfg.DISTILLER.TYPE == "NONE":
        if cfg.DATASET.TYPE == "imagenet":
            model_student = imagenet_model_dict[cfg.DISTILLER.STUDENT](
                pretrained=False)
        else:
            model_student = cifar_model_dict[cfg.DISTILLER.STUDENT][0](
                num_classes=num_classes
            )
        distiller = distiller_dict[cfg.DISTILLER.TYPE](model_student)
    # distillation
    else:
        print(log_msg("Loading teacher model", "INFO"))
        if cfg.DATASET.TYPE == "imagenet":
            model_teacher = imagenet_model_dict[cfg.DISTILLER.TEACHER](
                pretrained=True)
            model_student = imagenet_model_dict[cfg.DISTILLER.STUDENT](
                pretrained=False)
        else:
            if cfg.DATASET.ENHANCE_AUGMENT:
                net, pretrain_model_path = cifar_aug_model_dict[cfg.DISTILLER.TEACHER]
            else:
                net, pretrain_model_path = cifar_model_dict[cfg.DISTILLER.TEACHER]
            assert (
                pretrain_model_path is not None
            ), "no pretrain model for teacher {}".format(cfg.DISTILLER.TEACHER)
            model_teacher = net(num_classes=num_classes)
            model_teacher.load_state_dict(
                load_checkpoint(pretrain_model_path)["model"])
            model_student = cifar_model_dict[cfg.DISTILLER.STUDENT][0](
                num_classes=num_classes
            )
        if cfg.DISTILLER.TYPE == "CRD":
            distiller = distiller_dict[cfg.DISTILLER.TYPE](
                model_student, model_teacher, cfg, num_data
            )
        else:
            distiller = distiller_dict[cfg.DISTILLER.TYPE](
                model_student, model_teacher, cfg
            )
    distiller = torch.nn.DataParallel(distiller.cuda())

    if cfg.DISTILLER.TYPE != "NONE":
        print(
            log_msg(
                "Extra parameters of {}: {}\033[0m".format(
                    cfg.DISTILLER.TYPE, distiller.module.get_extra_parameters()
                ),
                "INFO",
            )
        )

    # training
    if group_flag:
        if id == "":
            id = "default"
        experiment_name = experiment_name+"_"+id+"_" + \
            datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    trainer = trainer_dict[cfg.SOLVER.TRAINER](
        experiment_name, distiller, train_loader, val_loader, cfg
    )
    trainer.train(resume=resume)


# for training with original python reqs
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser("training for knowledge distillation.")
    parser.add_argument("--cfg", type=str, default="")
    parser.add_argument("--group", action="store_true")
    parser.add_argument("--id", type=str, default="",
                        help="identifier for training instance")
    parser.add_argument("--record_loss", action="store_true")
    parser.add_argument("--suffix", type=str, nargs="?", default="", const="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--data_workers", type=int, default=None)
    parser.add_argument("opts", default=None, nargs=argparse.REMAINDER)

    args = parser.parse_args()
    cfg.merge_from_file(args.cfg)
    cfg.merge_from_list(args.opts)

    if args.suffix != "":
        cfg.EXPERIMENT.TAG += ","+args.suffix

    if args.record_loss:
        if cfg.DISTILLER.TYPE == "CRD":
            cfg.SOLVER.TRAINER = "custom_crd"
        else:
            cfg.SOLVER.TRAINER = "custom"

    if args.data_workers is not None:
        cfg.DATASET.NUM_WORKERS = int(args.data_workers)

    if cfg.DATASET.ENHANCE_AUGMENT:
        cfg.EXPERIMENT.TAG += ",aug"

    if args.seed is not None:
        seed = args.seed
        torch.manual_seed(seed)
        torch.cuda.manual_seed(seed)
        np.random.seed(seed)
        random.seed(seed)
        cfg.EXPERIMENT.TAG += ",seed_"+str(seed)

    cfg.freeze()
    main(cfg, args.resume, args.opts, group_flag=args.group, id=args.id)
