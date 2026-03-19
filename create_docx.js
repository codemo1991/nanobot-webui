const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType } = require('docx');
const fs = require('fs');

const doc = new Document({
  styles: {
    default: {
      document: {
        run: { font: "Arial", size: 24 } // 12pt default
      }
    },
    paragraphStyles: [
      {
        id: "Heading1",
        name: "Heading 1",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 48, bold: true, font: "Arial" },
        paragraph: { spacing: { before: 240, after: 240 }, outlineLevel: 0 }
      },
      {
        id: "Heading2",
        name: "Heading 2",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 36, bold: true, font: "Arial" },
        paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 1 }
      },
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
      }
    },
    children: [
      // 标题
      new Paragraph({
        heading: HeadingLevel.HEADING_1,
        children: [new TextRun('桌面截图内容描述')]
      }),

      // 概述
      new Paragraph({
        heading: HeadingLevel.HEADING_2,
        children: [new TextRun('概述')]
      }),
      new Paragraph({
        children: [new TextRun('这张图片展示了一个电脑桌面的截图，背景是一幅色彩柔和的火烈鸟群在水边栖息的场景。天空呈现出日落时分的橙黄色渐变，水面反射着温暖的光线，营造出一种宁静而美丽的氛围。前景中有几只火烈鸟清晰可见，它们的羽毛呈粉红色，长颈弯曲，姿态优雅。')]
      }),

      // 背景描述
      new Paragraph({
        heading: HeadingLevel.HEADING_2,
        children: [new TextRun('主要元素分析')]
      }),
      new Paragraph({
        children: [new TextRun({ text: '1. 背景', bold: true })]
      }),
      new Paragraph({
        children: [new TextRun('背景是一张自然风景照片，主体为一群火烈鸟，它们站在浅水中，远处还有更多的火烈鸟模糊地分布在画面中。天空的颜色从顶部的淡蓝色过渡到靠近地平线的橙黄色，显示出日落或日出时的景象。水面平静，反射出天空和火烈鸟的倒影，增加了画面的层次感。')]
      }),

      // 桌面图标
      new Paragraph({
        children: [new TextRun({ text: '2. 桌面图标', bold: true })]
      }),
      new Paragraph({
        children: [new TextRun('桌面上排列着大量的应用程序图标，覆盖了整个屏幕的左侧和中部区域。这些图标包括各种软件和工具，如浏览器（Microsoft Edge、Google Chrome）、开发工具（IntelliJ IDEA、Visual Studio Code）、办公软件（WPS Office、DeepNote）、文件管理器（Navicat Premium、DataGrip）等。图标的设计风格多样，有些是简洁的扁平化设计，有些则带有更复杂的图形元素。部分图标旁边有中文标签，例如"MongoDB Compass"、"Cherry Studio"、"义凌安全文件"等，表明这些软件可能与中文用户相关。')]
      }),

      // 任务栏
      new Paragraph({
        children: [new TextRun({ text: '3. 任务栏', bold: true })]
      }),
      new Paragraph({
        children: [new TextRun('任务栏位于屏幕底部，显示了多个打开的窗口，每个窗口都以缩略图的形式呈现。这些窗口大多是文件资源管理器或代码编辑器，显示了文件夹和代码内容。任务栏右侧显示了系统的状态信息，包括时间（9:55）、日期（2026/2/29）、温度（14°C）以及一些系统图标（如网络连接、音量控制等）。')]
      }),

      // 文本内容
      new Paragraph({
        children: [new TextRun({ text: '4. 文本内容', bold: true })]
      }),
      new Paragraph({
        children: [new TextRun('在屏幕右上角有一个小窗口，显示了一段中文文字："了解有关此图片的信息"，这可能是操作系统提供的一个功能，用于查看图片的详细信息。桌面上的图标和任务栏中的窗口标题栏中也包含了一些中文文本，如"本地磁盘 (D:) - 文件资源管理器"、"桌面 - 文件资源管理器"等。')]
      }),

      // 颜色和视觉风格
      new Paragraph({
        children: [new TextRun({ text: '5. 颜色和视觉风格', bold: true })]
      }),
      new Paragraph({
        children: [new TextRun('整体色调以暖色为主，尤其是火烈鸟的粉红色和天空的橙黄色，给人一种温馨的感觉。桌面背景的模糊效果使得前景中的火烈鸟更加突出，形成了视觉焦点。应用程序图标的颜色和设计风格各异，但整体上保持了和谐的统一性。')]
      }),

      // 其他细节
      new Paragraph({
        children: [new TextRun({ text: '6. 其他细节', bold: true })]
      }),
      new Paragraph({
        children: [new TextRun('屏幕左下角显示了当前的天气信息，温度为14°C，地点为"局部晴朗"。任务栏上的搜索框和开始按钮表明这是一个Windows操作系统的桌面。右下角的时间显示为9:55，日期为2026年2月29日，这是一个闰年的日期，较为特殊。')]
      }),

      // 结论
      new Paragraph({
        heading: HeadingLevel.HEADING_2,
        children: [new TextRun('结论与观察')]
      }),
      new Paragraph({
        children: [new TextRun('这张桌面截图展示了一个典型的个人电脑工作环境，背景是一幅美丽的自然风景照片，突出了火烈鸟的形象。桌面上的应用程序图标种类繁多，涵盖了开发、办公、娱乐等多个领域，表明用户可能是一个多任务处理能力强的人，经常需要使用多种工具进行工作或学习。任务栏中的多个打开窗口显示了用户正在进行文件管理和代码编辑等工作，进一步印证了这一点。整体布局整洁有序，图标排列整齐，反映出用户对桌面管理有一定的偏好和习惯。')]
      }),
    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync('1.docx', buffer);
  console.log('文档已成功创建: 1.docx');
});
