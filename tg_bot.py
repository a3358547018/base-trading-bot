"""
tg_bot.py — Telegram 控制面板

功能：
  1. 实时查看交易状态
  2. 动态调整交易参数（无需重启）
  3. 钱包余额查询
  4. 手动触发交易/转账/领取手续费
  5. 暂停/恢复执行器
  6. 加密钱包私钥
"""

import logging
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters
)

from config import TELEGRAM_TOKEN, TELEGRAM_ADMIN_ID, WETH_ADDRESS
import executor
import scheduler
import ledger
import wallet_manager
import rpc_client
from overrides import get_param, set_param, reset_param, get_all_params, reset_all

logger = logging.getLogger(__name__)

# 对话状态常量
SET_PARAM, SET_VALUE = range(2)


# ═══════════════════════════════════════════════════════════════
#  权限检查
# ═══════════════════════════════════════════════════════════════

async def _check_admin(update: Update) -> bool:
    """验证是否为管理员。"""
    if not TELEGRAM_ADMIN_ID:
        return True  # 未设置时允许所有
    return update.effective_user.id == TELEGRAM_ADMIN_ID


async def _deny_access(update: Update) -> None:
    """拒绝访问。"""
    await update.message.reply_text("❌ 权限不足。仅管理员可操作。")


# ═══════════════════════════════════════════════════════════════
#  命令处理器
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """启动命令。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    text = """🤖 **Base 链自动交易机器人控制面板**

📊 **主要命令：**
/status — 查看运行状态
/wallets — 钱包信息
/schedule — 时间表统计
/ledger — 今日账本
/params — 查看所有参数
/set_param — 修改参数
/reset_param — 重置参数
/pause — 暂停执行器
/resume — 恢复执行器
/refresh_tokens — 重新拉取代币
/refresh_strangers — 重新构建陌生地址池
/manual_action — 手动触发事件

⚙️ 使用 /help 查看详细帮助
    """
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """帮助命令。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    text = """📖 **详细帮助**

**查看状态：**
  /status — 运行状态（运行中/已暂停）
  /wallets — 列出22个钱包地址 & 余额
  /schedule — 今日待执行事件统计
  /ledger — 今日交易明细
  /context — 运行时上下文（代币数、陌生地址数等）

**修改参数（实时生效，无需重启）：**
  /params — 查看所有可调参数
  /set_param — 交互式修改参数
  /reset_param — 重置单个参数
  /reset_all — 重置全部参数

**控制执行器：**
  /pause — ⏸ 暂停交易执行（时间表仍保留）
  /resume — ▶ 恢复交易执行
  /enable_swap — 启用 swap 事件
  /disable_swap — 禁用 swap 事件
  /enable_transfer — 启用 transfer 事件
  /disable_transfer — 禁用 transfer 事件
  /enable_claim — 启用 claim_fee 事件
  /disable_claim — 禁用 claim_fee 事件

**手动操作：**
  /manual_action — 选择钱包和操作类型，立即执行
  /refresh_tokens — 重新拉取代币池
  /refresh_strangers — 重新构建陌生地址池

**清空时间表：**
  /clear_schedule — 删除所有待执行事件
  """
    await update.message.reply_text(text, parse_mode="Markdown")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看运行状态。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    running = executor.is_running()
    status_text = "▶️ 运行中" if running else "⏸️ 已暂停"
    enabled = executor.get_enabled()

    text = f"""📊 **执行器状态**

状态: {status_text}
Swap: {'✅' if enabled['swap'] else '❌'}
Transfer: {'✅' if enabled['transfer'] else '❌'}
Claim Fee: {'✅' if enabled['claim_fee'] else '❌'}
    """
    await update.message.reply_text(text, parse_mode="Markdown")


async def wallets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """列出所有钱包。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    ctx = executor.get_context()
    wallet_count = ctx.get("wallet_count", 0)

    if wallet_count == 0:
        await update.message.reply_text("❌ 无有效钱包")
        return

    try:
        w3 = rpc_client.get_w3()
        wallets = wallet_manager.load_wallets()

        text = f"💼 **钱包列表** (共 {len(wallets)} 个)\n\n"
        for w in wallets[:10]:  # 显示前10个
            balance = wallet_manager.get_eth_balance(w3, w["address"])
            text += f"#{w['index']:02d}: `{w['address'][:10]}...{w['address'][-4:]}`\n"
            text += f"       余额: {balance:.6f} ETH\n"

        if len(wallets) > 10:
            text += f"\n... 还有 {len(wallets) - 10} 个钱包\n"

        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 错误: {e}")


async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看时间表统计。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    stats = scheduler.get_stats()
    text = f"""⏰ **时间表统计**

总事件: {stats['total']}
待执行: {stats['pending']}
已过期: {stats['past']}

**按类型统计：**
Swap: {stats['by_type'].get('swap', 0)}
Transfer: {stats['by_type'].get('transfer', 0)}
Claim: {stats['by_type'].get('claim_fee', 0)}

**下一个事件：**
"""
    if stats.get('next'):
        next_ev = stats['next']
        text += f"{next_ev['time']} | 钱包#{next_ev['wallet']:02d} | {next_ev['type']}"
    else:
        text += "无待执行事件"

    await update.message.reply_text(text, parse_mode="Markdown")


async def ledger_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看今日账本。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    summary = ledger.get_today_summary()
    text = f"""📖 **今日账本**

日期: {summary['date']}
总交易笔数: {summary['total_txs']}

买入: {summary['total_buys']}
卖出: {summary['total_sells']}
转账: {summary['total_transfers']}
领取手续费: {summary['total_claims']}

**各钱包交易笔数：**
"""
    wallets_info = summary.get('wallets', {})
    for widx in sorted(wallets_info.keys()):
        count = wallets_info[widx]
        text += f"钱包 #{widx:02d}: {count} 笔\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def context_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看运行时上下文。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    ctx = executor.get_context()
    text = f"""⚙️ **运行时上下文**

钱包数: {ctx.get('wallet_count', 0)}
代币数: {ctx.get('token_count', 0)}
陌生地址数: {ctx.get('stranger_count', 0)}

**代币分配样本：**
"""
    allocation = ctx.get('allocation', {})
    for widx in list(allocation.keys())[:5]:
        tokens = allocation[widx]
        symbols = ', '.join([t for t in tokens[:3]])
        text += f"钱包 #{widx:02d}: {symbols}...\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def params_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看所有参数。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    params = get_all_params()
    text = "📋 **当前参数**\n\n"
    for key in sorted(params.keys()):
        val = params[key]
        text += f"`{key}`: {val}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def set_param_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """开始参数修改对话。"""
    if not await _check_admin(update):
        await _deny_access(update)
        return ConversationHandler.END

    params = get_all_params()
    text = "选择要修改的参数:\n\n"
    for key in sorted(params.keys()):
        text += f"`{key}`\n"
    text += "\n请输入参数名称:"

    await update.message.reply_text(text, parse_mode="Markdown")
    return SET_PARAM


async def set_param_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """获取参数名称。"""
    key = update.message.text.strip().upper()
    params = get_all_params()

    if key not in params:
        await update.message.reply_text(f"❌ 参数 '{key}' 不存在")
        return SET_PARAM

    context.user_data['param_key'] = key
    current = params[key]
    await update.message.reply_text(
        f"参数: `{key}`\n当前值: `{current}`\n\n请输入新值:",
        parse_mode="Markdown"
    )
    return SET_VALUE


async def set_param_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """设置参数值。"""
    key = context.user_data.get('param_key')
    value_str = update.message.text.strip()

    try:
        # 尝试转换为数字
        if '.' in value_str:
            value = float(value_str)
        else:
            value = int(value_str)
    except ValueError:
        value = value_str

    if set_param(key, value):
        await update.message.reply_text(f"✅ 已设置 `{key}` = `{value}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ 设置失败")

    return ConversationHandler.END


async def reset_param_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """重置单个参数。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    if not context.args:
        await update.message.reply_text("用法: /reset_param <参数名>")
        return

    key = context.args[0].upper()
    if reset_param(key):
        await update.message.reply_text(f"✅ 已重置 `{key}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ 参数 '{key}' 不存在或未被覆盖")


async def reset_all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """重置所有参数。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    reset_all()
    await update.message.reply_text("✅ 已重置所有参数")


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """暂停执行器。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    executor.set_running(False)
    await update.message.reply_text("⏸️ 已暂停执行器")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """恢复执行器。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    executor.set_running(True)
    await update.message.reply_text("▶️ 已恢复执行器")


async def enable_swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return await _deny_access(update)
    executor.set_enabled('swap', True)
    await update.message.reply_text("✅ Swap 事件已启用")


async def disable_swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return await _deny_access(update)
    executor.set_enabled('swap', False)
    await update.message.reply_text("❌ Swap 事件已禁用")


async def enable_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return await _deny_access(update)
    executor.set_enabled('transfer', True)
    await update.message.reply_text("✅ Transfer 事件已启用")


async def disable_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return await _deny_access(update)
    executor.set_enabled('transfer', False)
    await update.message.reply_text("❌ Transfer 事件已禁用")


async def enable_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return await _deny_access(update)
    executor.set_enabled('claim_fee', True)
    await update.message.reply_text("✅ Claim Fee 事件已启用")


async def disable_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return await _deny_access(update)
    executor.set_enabled('claim_fee', False)
    await update.message.reply_text("❌ Claim Fee 事件已禁用")


async def refresh_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """重新拉取代币。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    count = executor.refresh_tokens()
    await update.message.reply_text(f"✅ 已刷新代币池，共 {count} 个代币")


async def refresh_strangers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """重新构建陌生地址池。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    count = executor.refresh_strangers()
    await update.message.reply_text(f"✅ 已刷新陌生地址池，共 {count} 个地址")


async def manual_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """手动触发事件。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    keyboard = []
    for widx in range(22):
        keyboard.append([InlineKeyboardButton(f"钱包 #{widx:02d}", callback_data=f"wallet_{widx}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("选择钱包:", reply_markup=reply_markup)


async def wallet_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """钱包选中回调。"""
    query = update.callback_query
    await query.answer()

    wallet_idx = int(query.data.split('_')[1])
    context.user_data['manual_wallet'] = wallet_idx

    keyboard = [
        [InlineKeyboardButton("Swap", callback_data="action_swap")],
        [InlineKeyboardButton("Buy", callback_data="action_buy")],
        [InlineKeyboardButton("Sell", callback_data="action_sell")],
        [InlineKeyboardButton("Transfer", callback_data="action_transfer")],
        [InlineKeyboardButton("Claim Fee", callback_data="action_claim_fee")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"钱包 #{wallet_idx:02d}\n\n选择操作:",
        reply_markup=reply_markup
    )


async def action_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """操作选中回调。"""
    query = update.callback_query
    await query.answer()

    action = query.data.split('_')[1]
    wallet_idx = context.user_data.get('manual_wallet', 0)

    executor.enqueue_manual(wallet_idx, action)
    await query.edit_message_text(f"✅ 已入队: 钱包 #{wallet_idx:02d} {action}")


async def clear_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """清空时间表。"""
    if not await _check_admin(update):
        return await _deny_access(update)

    count = scheduler.clear_schedule()
    await update.message.reply_text(f"✅ 已清空时间表，删除 {count} 个事件")


# ═══════════════════════════════════════════════════════════════
#  运行
# ═══════════════════════════════════════════════════════════════

def run_bot():
    """启动 Telegram Bot。"""
    logger.info("🤖 Telegram Bot 启动")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # 对话处理器（参数修改）
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('set_param', set_param_start)],
        states={
            SET_PARAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_param_name)],
            SET_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_param_value)],
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: ConversationHandler.END)],
    )

    # 命令处理器
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('wallets', wallets))
    app.add_handler(CommandHandler('schedule', schedule_cmd))
    app.add_handler(CommandHandler('ledger', ledger_cmd))
    app.add_handler(CommandHandler('context', context_cmd))
    app.add_handler(CommandHandler('params', params_cmd))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('reset_param', reset_param_cmd))
    app.add_handler(CommandHandler('reset_all', reset_all_cmd))
    app.add_handler(CommandHandler('pause', pause))
    app.add_handler(CommandHandler('resume', resume))
    app.add_handler(CommandHandler('enable_swap', enable_swap))
    app.add_handler(CommandHandler('disable_swap', disable_swap))
    app.add_handler(CommandHandler('enable_transfer', enable_transfer))
    app.add_handler(CommandHandler('disable_transfer', disable_transfer))
    app.add_handler(CommandHandler('enable_claim', enable_claim))
    app.add_handler(CommandHandler('disable_claim', disable_claim))
    app.add_handler(CommandHandler('refresh_tokens', refresh_tokens))
    app.add_handler(CommandHandler('refresh_strangers', refresh_strangers))
    app.add_handler(CommandHandler('manual_action', manual_action))
    app.add_handler(CommandHandler('clear_schedule', clear_schedule))

    # 回调处理器
    app.add_handler(CallbackQueryHandler(wallet_selected, pattern='^wallet_'))
    app.add_handler(CallbackQueryHandler(action_selected, pattern='^action_'))

    # 启动
    app.run_polling()
