import argparse
from tqdm import tqdm

import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn

from collections import defaultdict
from pathlib import Path
import numpy as np

from mdistiller.engine.cfg import show_cfg, dump_cfg
from mdistiller.engine.cfg import CFG as cfg
from mdistiller.engine.utils import (
    load_checkpoint, log_msg, AverageMeter, accuracy)
from mdistiller.models import get_model

from .datasets import get_dataset


cudnn.benchmark = False


def accuracy(output, target, topk=(1,)):
    with torch.no_grad():
        maxk = max(topk)
        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.reshape(1, -1).expand_as(pred))

        correct_flags_list = []
        for k in topk:
            correct_flags_list.append(correct[:k].any(dim=0))
    return correct_flags_list


def validate_by_category(dataloader, model, num_classes):
    model.eval()

    logits_dict = defaultdict(list)
    feats_dict = defaultdict(list)
    correct_dict = defaultdict(list)

    pbar = tqdm(total=len(dataloader))
    with torch.no_grad():
        for i, data in enumerate(dataloader):
            image, target = data[:2]
            image = image.float()
            image = image.cuda(non_blocking=True)
            # target = target.cuda(non_blocking=True)
            logits, feats = model(image)
            logits = logits.cpu()
            pooled_feat = feats['pooled_feat'].cpu()

            correct_flags, = accuracy(logits, target, topk=(1,))

            for j in range(num_classes):
                logits_dict[j].append(logits[target == j])
                feats_dict[j].append(pooled_feat[target == j])
                correct_dict[j].append(correct_flags[target == j])

            pbar.update()
    pbar.close()

    for i in range(num_classes):
        correct_dict[i] = torch.cat(correct_dict[i]).to(dtype=torch.float32)

    for i in range(num_classes):
        acc = torch.mean(correct_dict[i])
        print(f"Class {i} accuracy: {acc:.4f}")

    currect_tuple = tuple(correct_dict.values())
    acc = torch.mean(torch.cat(currect_tuple))
    print(f"Total accuracy: {acc:.4f}")

    res_logits = {}
    res_feats = {}
    for i in range(num_classes):
        res_logits[f"class{i}"] = torch.concat(logits_dict[i]).numpy()
        res_feats[f"class{i}"] = torch.concat(feats_dict[i]).numpy()

    return res_logits, res_feats


def validate(dataloader, model, num_classes, save_path, store_feats=False, bucket_size=None):
    model.eval()

    logits_arr = []
    labels_arr = []

    if store_feats:
        feats_arr = []

    correct_dict = defaultdict(list)
    bucket_id = 0

    def save_result(path, bucket_id=None):
        res = dict(
            logits=torch.cat(logits_arr).numpy(),
            labels=torch.cat(labels_arr).numpy()
        )

        if store_feats:
            res['feats'] = torch.cat(feats_arr).numpy()

        if bucket_id is not None:
            filename = path.stem + \
                f"_bucket{bucket_id}" + path.suffix

            path = path.parent/filename
        np.savez_compressed(path, **res)

    def reset():
        logits_arr.clear()
        labels_arr.clear()
        if store_feats:
            feats_arr.clear()

    pbar = tqdm(total=len(dataloader))
    with torch.no_grad():
        for i, data in enumerate(dataloader, 1):
            image, target = data[:2]
            image = image.float().cuda(non_blocking=True)
            # target = target.cuda(non_blocking=True)
            logits, feats = model(image)
            logits = logits.cpu()

            correct_flags, = accuracy(logits, target, topk=(1,))

            logits_arr.append(logits)
            labels_arr.append(target)
            if store_feats:
                pooled_feat = feats['pooled_feat'].cpu()
                feats_arr.append(pooled_feat)

            for j in range(num_classes):
                correct_dict[j].append(correct_flags[target == j])

            if bucket_size is not None and i % bucket_size == 0:
                save_result(save_path, bucket_id=bucket_id)
                reset()
                bucket_id += 1

            pbar.update()
    pbar.close()

    for i in range(num_classes):
        correct_dict[i] = torch.cat(correct_dict[i]).to(dtype=torch.float32)

    for i in range(num_classes):
        acc = torch.mean(correct_dict[i])
        print(f"Class {i} accuracy: {acc:.4f}")

    currect_tuple = tuple(correct_dict.values())
    acc = torch.mean(torch.cat(currect_tuple))
    print(f"Total accuracy: {acc:.4f}")

    if bucket_size is not None:
        if len(logits_arr) > 0:
            save_result(save_path, bucket_id)
    else:
        save_result(save_path)


def get_filename(cfg, args):
    filename = f'{cfg.DATASET.TYPE}_{args.save_name}'

    if cfg.DATASET.ENHANCE_AUGMENT:
        filename += "_aug"

    if args.train:
        if args.val_transform:
            filename += "_train(val_t)"
        else:
            filename += "_train"
    else:
        filename += "_val"

    filename += ".npz"

    return filename


def main(cfg, args):

    show_cfg(cfg)
    dataloader, num_classes = get_dataset(
        cfg,
        train=args.train,
        use_val_transform=args.val_transform
    )

    print(log_msg("Loading model", "INFO"))

    model_name = cfg.DISTILLER.TEACHER

    model = get_model(cfg, model_name, pretrained=False)
    model.load_state_dict(
        load_checkpoint(args.model_path)
    )

    model.cuda()

    save_path = Path(args.save_dir).expanduser()
    if not save_path.exists():
        save_path.mkdir(parents=True)
    save_path = save_path / get_filename(cfg, args)

    validate(dataloader, model, num_classes,
             save_path, store_feats=not args.no_feats, bucket_size=args.bucket_size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=str, default="imagenet",
                        choices=['imagenet', 'cifar100', 'cifar100_aug', 'ti', 'cub2011'])
    parser.add_argument("--model", type=str, default="ResNet34")
    parser.add_argument("--model-path", type=str, default="")
    parser.add_argument("--train", action="store_true", help="use train set")
    parser.add_argument("--val-transform",
                        action="store_true", help="use val transform")
    parser.add_argument("--config", type=str)
    parser.add_argument("--save-dir", type=str, default="exp/kd_logits_data")
    # parser.add_argument("--save-prefix", type=str)
    parser.add_argument("--save-name", type=str)
    parser.add_argument("--no-feats", action="store_true")
    parser.add_argument("--bucket-size", type=int)
    parser.add_argument("opts", nargs="*")

    args = parser.parse_args()
    if args.dataset in ["imagenet", "ti", "cub2011"]:
        cfg_path = "tools/statistics/imagenet.yaml"
    elif args.dataset == "cifar100":
        cfg_path = "tools/statistics/cifar100.yaml"
    elif args.dataset == "cifar100_aug":
        cfg_path = "tools/statistics/cifar100_aug.yaml"
    else:
        raise NotImplementedError(args.dataset)

    if args.config is not None:
        cfg.merge_from_other_cfg(Path(args.config).expanduser())
    else:
        cfg.merge_from_file(cfg_path)

    cfg.merge_from_list(args.opts)
    cfg.DISTILLER.TYPE = "NONE"
    cfg.DISTILLER.TEACHER = args.model
    # cfg.merge_from_list(args.opts)
    if args.dataset in ["ti", "cub2011"]:
        cfg.DATASET.TYPE = args.dataset

    cfg.freeze()
    main(cfg, args)
