"""ResNet-20 for CIFAR-10 (He et al. 2016, section 4.2), with the stubs eager-mode INT8 quantization needs.

Shortcuts that change shape are 1x1 convolutions with batch norm (the paper's option B), so every op has a
quantized kernel in fbgemm."""
import torch
from torch import nn
from torch.ao.nn.quantized import FloatFunctional
from torch.ao.quantization import DeQuantStub, QuantStub, fuse_modules


class BasicBlock(nn.Module):
    def __init__(self, cin, cout, stride):
        super().__init__()
        self.conv1 = nn.Conv2d(cin, cout, 3, stride, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(cout)
        self.relu1 = nn.ReLU()
        self.conv2 = nn.Conv2d(cout, cout, 3, 1, 1, bias=False)
        self.bn2 = nn.BatchNorm2d(cout)
        self.shortcut = nn.Sequential()
        if stride != 1 or cin != cout:
            self.shortcut = nn.Sequential(nn.Conv2d(cin, cout, 1, stride, bias=False), nn.BatchNorm2d(cout))
        self.skip = FloatFunctional()            # add + relu, quantized as one op after convert

    def forward(self, x):
        out = self.relu1(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return self.skip.add_relu(out, self.shortcut(x))

    def fuse(self):
        fuse_modules(self, [["conv1", "bn1", "relu1"], ["conv2", "bn2"]], inplace=True)
        if len(self.shortcut):
            fuse_modules(self.shortcut, [["0", "1"]], inplace=True)


class ResNet20(nn.Module):
    def __init__(self, num_classes=10, blocks=3):
        super().__init__()
        self.quant = QuantStub()
        self.conv1 = nn.Conv2d(3, 16, 3, 1, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(16)
        self.relu1 = nn.ReLU()
        layers, cin = [], 16
        for cout, stride in ((16, 1), (32, 2), (64, 2)):
            stage = []
            for i in range(blocks):
                stage.append(BasicBlock(cin, cout, stride if i == 0 else 1))
                cin = cout
            layers.append(nn.Sequential(*stage))
        self.layer1, self.layer2, self.layer3 = layers
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Linear(64, num_classes)
        self.dequant = DeQuantStub()
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")

    def forward(self, x):
        x = self.quant(x)
        x = self.relu1(self.bn1(self.conv1(x)))
        x = self.layer3(self.layer2(self.layer1(x)))
        x = torch.flatten(self.pool(x), 1)
        return self.dequant(self.fc(x))

    def fuse(self):
        """Folds each batch norm into its convolution; call in eval mode, before prepare."""
        fuse_modules(self, [["conv1", "bn1", "relu1"]], inplace=True)
        for stage in (self.layer1, self.layer2, self.layer3):
            for block in stage:
                block.fuse()


def parameter_count(model):
    return sum(p.numel() for p in model.parameters())
