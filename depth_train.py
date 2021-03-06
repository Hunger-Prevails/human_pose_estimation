import os
import json
import utils
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import torch.optim as optim
import importlib


root_me = os.path.join(os.sep, 'globalwork', 'liu')


def get_loader(args):
    with open(os.path.join(root_me, 'metadata.json')) as file:
        metadata = json.load(file)

    return importlib.import_module(metadata['loader'][args.data_name])


def wrap_by_name(names, params):
    param_convs = [param for name, param in zip(names, params) if 'bn' in name]
    param_bns = [param for name, param in zip(names, params) if 'bn' not in name]
    return [dict(params = param_convs), dict(params = param_bns)]


def to_test_worker(test_loader, no_depth, depth_only):

    if no_depth:
        for in_image, true_cam, true_val, color_br in test_loader:
            yield in_image, true_cam, true_val, color_br
    else:
        for color_image, depth_image, true_cam, true_val, color_br in test_loader:
            in_image = depth_image if depth_only else color_image
            yield in_image, true_cam, true_val, color_br
    return


class Trainer:

    def __init__(self, args, model, data_info):
        self.model = model
        self.data_info = data_info

        self.list_params = [param for name, param in model.named_parameters()]
        self.list_names = [name for name, param in model.named_parameters()]

        self.half_acc = args.half_acc
        self.depth_only = args.depth_only
        self.do_fusion = args.do_fusion
        self.do_teach = args.do_teach
        self.semi_teach = args.semi_teach
        self.sigmoid = args.sigmoid
        self.bin_dist = args.bin_dist
        self.do_freeze = args.do_freeze

        with open(os.path.join(root_me, 'metadata.json')) as file:
            metadata = json.load(file)

        self.no_depth = metadata['no_depth'][args.data_name]
        self.thresh = metadata['thresholds'][args.data_name]

        self.save_last = args.save_last
        self.last_path = os.path.join(root_me, 'last_' + args.data_name, args.suffix)

        if args.semi_teach:
            args.data_name = 'pku'
            args.batch_size = args.semi_batch
            self.semi_loader = get_loader(args).data_loader(args, 'train', data_info)
            self.semi_worker = iter(self.semi_loader)

        if args.half_acc:
            self.copy_params = [param.clone().detach() for param in self.list_params]
            self.model = self.model.half()

            for param in self.copy_params:
                param.requires_grad = True
                param.grad = param.data.new_zeros(param.size())

            self.optimizer = optim.Adam(wrap_by_name(self.list_names, self.copy_params), args.learn_rate, weight_decay = args.weight_decay)
        else:
            self.optimizer = optim.Adam(wrap_by_name(self.list_names, self.list_params), args.learn_rate, weight_decay = args.weight_decay)

        self.depth = args.depth
        self.num_joints = args.num_joints
        self.side_in = args.side_in
        self.stride = args.stride
        self.depth_range = args.depth_range

        self.warmup = args.warmup
        self.learn_rate = args.learn_rate
        self.learn_decay = args.learn_decay
        self.num_epochs = args.n_epochs

        self.warmup_factor = args.warmup_factor
        self.alpha_dest = args.alpha_dest
        self.alpha_init = args.alpha_init
        self.alpha_span = args.alpha_span
        self.grad_norm = args.grad_norm
        self.grad_scaling = args.grad_scaling
        self.loss_div = args.loss_div

        self.criterion = nn.__dict__[args.criterion + 'Loss'](reduction = 'mean').cuda()


    def set_teacher(self, teacher):
        self.teacher = teacher.half() if self.half_acc else teacher


    def to(self, image, device):
        return image.half().to(device) if self.half_acc else image.to(device)


    def distill(self, batch, teach_last, last_feat, atten_map):
        if self.bin_dist:
            diff = F.binary_cross_entropy_with_logits(last_feat, torch.sigmoid(teach_last))  # (batch, 1024, 17, 17)

            diff = torch.mul(diff, atten_map)

            dist_loss = torch.sum(diff.view(batch, -1), dim = -1).mean()
        else:
            diff = (torch.sigmoid(teach_last) - torch.sigmoid(last_feat)) if self.sigmoid else (teach_last - last_feat)  # (batch, 1024, 17, 17)

            diff = torch.mul(diff, atten_map)

            dist_loss = torch.linalg.norm(diff.view(batch, -1), dim = -1).mean()

        return dist_loss


    def semi_train(self, device, i_batch):
        try:
            color_image, depth_image, true_cam, true_val, atten_map = next(self.semi_worker)
        except:
            self.semi_worker = iter(self.semi_loader)

            color_image, depth_image, true_cam, true_val, atten_map = next(self.semi_worker)

        color_image = self.to(color_image, device)
        depth_image = self.to(depth_image, device)
        atten_map = self.to(atten_map, device)

        semi_batch = true_cam.size(0)

        with torch.no_grad():
            teach_cam, teach_last = self.teach_infer(color_image, depth_image)

        cam_feat, last_feat = self.vanilla_infer(color_image, i_batch, True)

        dist_loss = self.distill(semi_batch, teach_last, last_feat, atten_map)

        return semi_batch, dist_loss


    def freeze_batchnorm(self):
        self.teacher.eval()
        self.model.freeze_batchnorm()


    def distill_train(self, epoch, data_loader, device):
        n_batches = len(data_loader)

        cam_loss_sum = 0.0
        dist_loss_sum = 0.0

        cam_loss_samples = 0
        dist_loss_samples = 0

        side_out = (self.side_in - 1) // self.stride + 1

        if self.do_freeze:
            self.freeze_batchnorm()

        print('\n=> alpha value: {:.2f}'.format(self.get_dist_weight(epoch)))

        for i_batch, batch_tuple in enumerate(data_loader):

            color_image, depth_image, true_cam, true_val, atten_map = batch_tuple

            color_image = self.to(color_image, device)
            depth_image = self.to(depth_image, device)
            atten_map = self.to(atten_map, device)

            true_cam = true_cam.to(device)
            true_val = true_val.to(device)

            full_batch = true_cam.size(0)

            with torch.no_grad():
                teach_cam, teach_last = self.teach_infer(color_image, depth_image)

            cam_feat, last_feat = self.vanilla_infer(color_image, i_batch, True)

            dist_loss = self.distill(full_batch, teach_last, last_feat, atten_map)

            heat_cam = utils.to_heatmap(cam_feat, self.depth, self.num_joints, side_out, side_out)

            key_index = self.data_info.key_index

            relat_cam = utils.decode(heat_cam, self.depth_range)

            relat_cam = relat_cam - relat_cam[:, key_index:key_index + 1]

            spec_cam = relat_cam + true_cam[:, key_index:key_index + 1]

            cam_loss = self.criterion(spec_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div, true_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div)

            cam_loss_sum += cam_loss.item() * full_batch
            cam_loss_samples += full_batch

            dist_loss_sum += dist_loss.item() * full_batch
            dist_loss_samples += full_batch

            message = '[=] train Epoch[{0}] Batch[{1}|{2}] '.format(epoch, i_batch, n_batches)
            message += ' Cam Loss {:.4f} '.format(cam_loss.item())
            message += ' Dist Loss {:.4f} '.format(dist_loss.item())

            loss = dist_loss * self.get_dist_weight(epoch) + cam_loss

            if self.semi_teach:
                semi_batch, semi_dist_loss = self.semi_train(device, i_batch)

                dist_loss_sum += semi_dist_loss.item() * semi_batch
                dist_loss_samples += semi_batch

                message += ' Semi Loss {:.4f}'.format(semi_dist_loss.item())

                loss += semi_dist_loss * self.get_dist_weight(epoch)

            print(message)

            if self.half_acc:
                loss *= self.grad_scaling

                for h_param in self.list_params:

                    if h_param.grad is None:
                        continue

                    h_param.grad.detach_()
                    h_param.grad.zero_()

                loss.backward()

                self.optimizer.zero_grad()

                do_update = True

                for c_param, h_param in zip(self.copy_params, self.list_params):

                    if h_param.grad is None:
                        continue

                    if torch.any(torch.isinf(h_param.grad)):
                        do_update = False
                        print('update step skipped')
                        break

                    c_param.grad.copy_(h_param.grad)
                    c_param.grad /= self.grad_scaling

                if do_update:
                    nn.utils.clip_grad_norm_(self.copy_params, self.grad_norm)

                    self.optimizer.step()

                    for c_param, h_param in zip(self.copy_params, self.list_params):
                        h_param.data.copy_(c_param.data)

            else:
                self.optimizer.zero_grad()
                loss.backward()

                nn.utils.clip_grad_norm_(self.list_params, self.grad_norm)
                self.optimizer.step()

        cam_loss_sum /= cam_loss_samples
        dist_loss_sum /= dist_loss_samples

        print('\n=> train Epoch[%d]  Cam Loss: %1.4f  Dist Loss: %1.4f\n\n' % (epoch, cam_loss_sum, dist_loss_sum))

        return dict(dist_train_loss = dist_loss_sum, cam_train_loss = cam_loss_sum)


    def fusion_train(self, epoch, data_loader, device):
        n_batches = len(data_loader)

        loss_avg = 0
        total = 0

        side_out = (self.side_in - 1) // self.stride + 1

        for i_batch, (color_image, depth_image, true_cam, true_val) in enumerate(data_loader):

            color_image = self.to(color_image, device)
            depth_image = self.to(depth_image, device)

            true_cam = true_cam.to(device)
            true_val = true_val.to(device)

            batch = true_cam.size(0)

            cam_feat = self.fusion_infer(color_image, depth_image, i_batch)

            heat_cam = utils.to_heatmap(cam_feat, self.depth, self.num_joints, side_out, side_out)

            key_index = self.data_info.key_index

            relat_cam = utils.decode(heat_cam, self.depth_range)

            relat_cam = relat_cam - relat_cam[:, key_index:key_index + 1]

            spec_cam = relat_cam + true_cam[:, key_index:key_index + 1]

            loss = self.criterion(spec_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div, true_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div)

            print('| train Epoch[%d] [%d/%d]  Loss %1.4f' % (epoch, i_batch, n_batches, loss.item()))

            loss_avg += loss.item() * batch

            total += batch

            if self.half_acc:
                loss *= self.grad_scaling

                for h_param in self.list_params:

                    if h_param.grad is None:
                        continue

                    h_param.grad.detach_()
                    h_param.grad.zero_()

                loss.backward()

                self.optimizer.zero_grad()

                do_update = True

                for c_param, h_param in zip(self.copy_params, self.list_params):

                    if h_param.grad is None:
                        continue

                    if torch.any(torch.isinf(h_param.grad)):
                        do_update = False
                        print('update step skipped')
                        break

                    c_param.grad.copy_(h_param.grad)
                    c_param.grad /= self.grad_scaling

                if do_update:
                    nn.utils.clip_grad_norm_(self.copy_params, self.grad_norm)

                    self.optimizer.step()

                    for c_param, h_param in zip(self.copy_params, self.list_params):
                        h_param.data.copy_(c_param.data)

            else:
                self.optimizer.zero_grad()
                loss.backward()

                nn.utils.clip_grad_norm_(self.list_params, self.grad_norm)
                self.optimizer.step()

        loss_avg /= total

        print('\n=> train Epoch[%d]  Cam Loss: %1.4f\n' % (epoch, loss_avg))

        return dict(cam_train_loss = loss_avg)


    def vanilla_train(self, epoch, data_loader, device):
        n_batches = len(data_loader)

        loss_avg = 0
        total = 0

        side_out = (self.side_in - 1) // self.stride + 1

        for i_batch, (color_image, depth_image, true_cam, true_val) in enumerate(data_loader):

            in_image = self.to(depth_image if self.depth_only else color_image, device)

            true_cam = true_cam.to(device)
            true_val = true_val.to(device)

            batch = true_cam.size(0)

            cam_feat = self.vanilla_infer(in_image, i_batch)

            heat_cam = utils.to_heatmap(cam_feat, self.depth, self.num_joints, side_out, side_out)

            key_index = self.data_info.key_index

            relat_cam = utils.decode(heat_cam, self.depth_range)

            relat_cam = relat_cam - relat_cam[:, key_index:key_index + 1]

            spec_cam = relat_cam + true_cam[:, key_index:key_index + 1]

            loss = self.criterion(spec_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div, true_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div)

            print('| train Epoch[%d] [%d/%d]  Loss %1.4f' % (epoch, i_batch, n_batches, loss.item()), flush = True)

            loss_avg += loss.item() * batch

            total += batch

            if self.half_acc:
                loss *= self.grad_scaling

                for h_param in self.list_params:

                    if h_param.grad is None:
                        continue

                    h_param.grad.detach_()
                    h_param.grad.zero_()

                loss.backward()

                self.optimizer.zero_grad()

                do_update = True

                for c_param, h_param in zip(self.copy_params, self.list_params):

                    if h_param.grad is None:
                        continue

                    if torch.any(torch.isinf(h_param.grad)):
                        do_update = False
                        print('update step skipped')
                        break

                    c_param.grad.copy_(h_param.grad)
                    c_param.grad /= self.grad_scaling

                if do_update:
                    nn.utils.clip_grad_norm_(self.copy_params, self.grad_norm)

                    self.optimizer.step()

                    for c_param, h_param in zip(self.copy_params, self.list_params):
                        h_param.data.copy_(c_param.data)

            else:
                self.optimizer.zero_grad()
                loss.backward()

                nn.utils.clip_grad_norm_(self.list_params, self.grad_norm)
                self.optimizer.step()

        loss_avg /= total

        print('\n=> train Epoch[%d]  Cam Loss: %1.4f\n' % (epoch, loss_avg))

        return dict(cam_train_loss = loss_avg)


    def train(self, epoch, data_loader):
        self.model.train()
        self.adapt_learn_rate(epoch)

        if self.do_teach:
            return self.distill_train(epoch, data_loader, torch.device('cuda'))
        elif self.do_fusion:
            return self.fusion_train(epoch, data_loader, torch.device('cuda'))
        else:
            return self.vanilla_train(epoch, data_loader, torch.device('cuda'))


    def fusion_test(self, epoch, test_loader, device):
        n_batches = len(test_loader)

        loss_avg = 0
        total = 0

        side_out = (self.side_in - 1) // self.stride + 1

        cam_stats = []

        for i_batch, (color_image, depth_image, true_cam, true_val, color_br) in enumerate(test_loader):

            color_image = self.to(color_image, device)
            depth_image = self.to(depth_image, device)

            true_cam = true_cam.to(device)
            true_val = true_val.to(device)

            batch = true_cam.size(0)

            with torch.no_grad():
                cam_feat = self.fusion_infer(color_image, depth_image, i_batch)

                heat_cam = utils.to_heatmap(cam_feat, self.depth, self.num_joints, side_out, side_out)

                key_index = self.data_info.key_index

                relat_cam = utils.decode(heat_cam, self.depth_range)

                relat_cam = relat_cam - relat_cam[:, key_index:key_index + 1]

                spec_cam = relat_cam + true_cam[:, key_index:key_index + 1]

                loss = self.criterion(spec_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div, true_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div)

            loss_avg += loss.item() * batch

            total += batch

            true_val = true_val.cpu().numpy().astype(np.bool)

            spec_cam = spec_cam.cpu().numpy()
            true_cam = true_cam.cpu().numpy()

            spec_cam = np.einsum('Bij,BCj->BCi', color_br, spec_cam)
            true_cam = np.einsum('Bij,BCj->BCi', color_br, true_cam)

            if self.save_last:
                utils.save_array(spec_cam, i_batch, self.last_path)

            cam_stats.append(utils.analyze(spec_cam, true_cam, true_val, self.data_info.mirror, self.thresh))

            print('| test Epoch[%d] [%d/%d]  Cam Loss %1.4f' % (epoch, i_batch, n_batches, loss.item()))

        loss_avg /= total

        record = dict(test_loss = loss_avg)
        record.update(utils.parse_epoch(cam_stats))

        print('\n=> test Epoch[%d]  Cam Loss: %1.4f\n' % (epoch, loss_avg))

        print('=>[SPEC] cam_mean: %1.3f  [pck]: %1.3f  [auc]: %1.3f\n' % (record['cam_mean'], record['score_pck'], record['score_auc']))

        return record


    def vanilla_test(self, epoch, test_loader, device):
        n_batches = len(test_loader)

        loss_avg = 0
        total = 0

        side_out = (self.side_in - 1) // self.stride + 1

        cam_stats = []

        test_worker = to_test_worker(test_loader, self.no_depth, self.depth_only)

        for i_batch, (in_image, true_cam, true_val, color_br) in enumerate(test_worker):

            in_image = self.to(in_image, device)

            true_cam = true_cam.to(device)
            true_val = true_val.to(device)

            batch = true_cam.size(0)

            with torch.no_grad():
                cam_feat = self.vanilla_infer(in_image, i_batch)

                heat_cam = utils.to_heatmap(cam_feat, self.depth, self.num_joints, side_out, side_out)

                key_index = self.data_info.key_index

                relat_cam = utils.decode(heat_cam, self.depth_range)

                relat_cam = relat_cam - relat_cam[:, key_index:key_index + 1]

                spec_cam = relat_cam + true_cam[:, key_index:key_index + 1]

                loss = self.criterion(spec_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div, true_cam.view(-1, 3)[true_val.view(-1)] / self.loss_div)

            loss_avg += loss.item() * batch

            total += batch

            true_val = true_val.cpu().numpy().astype(np.bool)

            spec_cam = spec_cam.cpu().numpy()
            true_cam = true_cam.cpu().numpy()

            spec_cam = np.einsum('Bij,BCj->BCi', color_br, spec_cam)
            true_cam = np.einsum('Bij,BCj->BCi', color_br, true_cam)

            if self.save_last:
                utils.save_array(spec_cam, i_batch, self.last_path)

            cam_stats.append(utils.analyze(spec_cam, true_cam, true_val, self.data_info.mirror, self.thresh))

            print('| test Epoch[%d] [%d/%d]  Cam Loss %1.4f' % (epoch, i_batch, n_batches, loss.item()))

        loss_avg /= total

        record = dict(test_loss = loss_avg)
        record.update(utils.parse_epoch(cam_stats))

        print('\n=> test Epoch[%d]  Cam Loss: %1.4f\n' % (epoch, loss_avg))

        print('=>[SPEC] cam_mean: %1.3f  [pck]: %1.3f  [auc]: %1.3f\n' % (record['cam_mean'], record['score_pck'], record['score_auc']))

        return record


    def test(self, epoch, test_loader):
        self.model.eval()

        if self.do_teach:
            return self.vanilla_test(epoch, test_loader, torch.device('cuda'))
        elif self.do_fusion:
            return self.fusion_test(epoch, test_loader, torch.device('cuda'))
        else:
            return self.vanilla_test(epoch, test_loader, torch.device('cuda'))


    def adapt_learn_rate(self, epoch):
        if epoch - 1 < self.warmup:
            learn_rate = self.learn_rate * self.warmup_factor

        elif epoch - 1 < 15:
            learn_rate = self.learn_rate

        elif epoch - 1 < 20:
            learn_rate = self.learn_rate * self.learn_decay

        elif epoch - 1 < 25:
            learn_rate = self.learn_rate * self.learn_decay * self.learn_decay

        else:
            learn_rate = self.learn_rate * self.learn_decay * self.learn_decay * self.learn_decay

        self.optimizer.param_groups[0]['lr'] = learn_rate
        self.optimizer.param_groups[1]['lr'] = learn_rate


    def get_dist_weight(self, epoch):
        alphas = np.linspace(self.alpha_init, self.alpha_dest, self.alpha_span)

        if epoch - 1 < self.alpha_span:
            return alphas[epoch - 1]
        else:
            return self.alpha_dest


    def vanilla_infer(self, in_image, i_batch, ret_last = False):
        cam_feat, last_feat = self.model(in_image)

        if self.half_acc:
            cam_feat = cam_feat.float()
            last_feat = last_feat.float()

        if self.save_last:
            utils.save_tensor(last_feat, i_batch, self.last_path)

        if ret_last:
            return cam_feat, last_feat
        else:
            return cam_feat


    def fusion_infer(self, color_image, depth_image, i_batch, ret_last = False):
        cam_feat, last_feat = self.model(color_image, depth_image)

        if self.half_acc:
            cam_feat = cam_feat.float()
            last_feat = last_feat.float()

        if self.save_last:
            utils.save_tensor(last_feat, i_batch, self.last_path)

        if ret_last:
            return cam_feat, last_feat
        else:
            return cam_feat


    def teach_infer(self, color_image, depth_image):
        if self.do_fusion:
            cam_feat, last_feat = self.teacher(color_image, depth_image)
        else:
            cam_feat, last_feat = self.teacher(depth_image if self.depth_only else color_image)

        if self.half_acc:
            return cam_feat.float(), last_feat.float()
        else:
            return cam_feat, last_feat
