"""Explicit Vega axes keep Chinese categories and periods horizontal."""
import streamlit as st


def _num(value: object) -> float:
    return float(str(value).replace(',', ''))


def _chart(pairs, *, mark, height):
    rows = [{'label': str(label), 'amount': _num(value)} for label, value in pairs if value is not None]
    if not rows:
        return
    st.vega_lite_chart(rows, {
        'mark': mark,
        'encoding': {
            'x': {'field': 'label', 'type': 'nominal', 'sort': None,
                  'axis': {'title': None, 'labelAngle': 0, 'labelLimit': 170, 'labelPadding': 12}},
            'y': {'field': 'amount', 'type': 'quantitative', 'axis': {'title': None, 'labelAngle': 0},
                  'scale': {'zero': True}},
            'tooltip': [{'field': 'label', 'type': 'nominal', 'title': '项目'},
                        {'field': 'amount', 'type': 'quantitative', 'title': '金额（元）', 'format': ',.2f'}]},
        'height': height, 'config': {'view': {'stroke': None}, 'axis': {'labelFontSize': 12}}
    }, width='stretch')


def bar(pairs, *, height=240):
    _chart(pairs, mark={'type': 'bar', 'color': '#a49be0', 'cornerRadiusTopLeft': 4,
                       'cornerRadiusTopRight': 4}, height=height)


def trend(series, *, height=260):
    _chart(list(series.items()), mark={'type': 'line', 'point': True, 'color': '#a49be0'}, height=height)
